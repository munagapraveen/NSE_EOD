import asyncio
from src.services.nse_client import NSEClient, HttpNotFoundError, HttpStatusError
from datetime import date, timedelta, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from loguru import logger

from src.models import Security, RawPrice, AdjustedPrice, Indicator, SyncLog
from src.services.stock_downloader import StockDownloader
from src.services.etf_downloader import ETFDownloader
from src.services.index_downloader import IndexDownloader
from src.services.corporate_actions import CorporateActionsService
from src.services.symbol_changes import SymbolChangesService
from src.services.price_adjuster import adjust_all_prices, adjust_prices_for_security, adjust_incremental_prices
from src.services.market_cap import calculate_all_historical_market_caps, calculate_historical_market_cap, calculate_incremental_market_caps_for_range
from src.services.indicators import calculate_all_indicators, calculate_incremental_indicators_for_range, calculate_indicators_for_security
from src.utils.backup_utils import create_db_backup, prune_old_backups



class SyncManager:
    """Orchestrates the full historical and daily incremental data synchronization workflow."""

    def __init__(self, client: NSEClient):
        self.client = client
        self.nse_client = client
        self.stock_downloader = StockDownloader(client)
        self.etf_downloader = ETFDownloader(client)
        self.index_downloader = IndexDownloader(client)
        self.ca_service = CorporateActionsService(client)
        self.sc_service = SymbolChangesService(client)
        self._cancel_requested = False
        self.is_running = False

    def _find_securities_with_adj_gaps(self, session: Session) -> set[int]:
        """Find security IDs that have raw prices but no corresponding adjusted prices on the same dates."""
        stmt = (
            select(RawPrice.security_id)
            .outerjoin(
                AdjustedPrice,
                (RawPrice.security_id == AdjustedPrice.security_id) & (RawPrice.trade_date == AdjustedPrice.trade_date)
            )
            .where(AdjustedPrice.security_id == None)
            .distinct()
        )
        return set(session.execute(stmt).scalars().all())

    def _find_securities_with_ind_gaps(self, session: Session) -> set[int]:
        """Find active security IDs that have adjusted prices but no corresponding indicators on the same dates."""
        stmt = (
            select(AdjustedPrice.security_id)
            .outerjoin(
                Indicator,
                (AdjustedPrice.security_id == Indicator.security_id) & (AdjustedPrice.trade_date == Indicator.trade_date)
            )
            .where(Indicator.security_id == None)
            .distinct()
        )
        return set(session.execute(stmt).scalars().all())

    def request_cancel(self):
        """Request cancellation of the current sync run."""
        self._cancel_requested = True

    async def run_sync(
        self,
        session: Session,
        start_date: date,
        end_date: date,
        options: dict,
        progress_callback=None
    ) -> dict:
        """Wrapper to manage the is_running state flag."""
        self.is_running = True
        try:
            return await self._run_sync_internal(
                session=session,
                start_date=start_date,
                end_date=end_date,
                options=options,
                progress_callback=progress_callback
            )
        finally:
            self.is_running = False

    async def _run_sync_internal(
        self,
        session: Session,
        start_date: date,
        end_date: date,
        options: dict,
        progress_callback=None
    ) -> dict:
        """
        Execute sync workflow date-by-date and stage-by-stage with resource release and backup orchestration.
        """
        sync_completed_ok = False
        try:
            from src.services.symbol_changes import temporary_index_drop
            with temporary_index_drop(session):
                summary = await self._run_sync_internal_impl(
                    session=session,
                    start_date=start_date,
                    end_date=end_date,
                    options=options,
                    progress_callback=progress_callback
                )
            if summary.get("status") in ("SUCCESS", "PARTIAL"):
                sync_completed_ok = True
            return summary
        finally:
            if hasattr(self, "nse_client") and self.nse_client:
                try:
                    await self.nse_client.close()
                except Exception as close_err:
                    logger.warning(f"Error closing nse_client session: {close_err}")
            from src.db.engine import engine as _engine
            _engine.dispose()

            # Create database backup after successful sync
            if sync_completed_ok:
                try:
                    logger.info("Sync completed successfully. Triggering automated database backup...")
                    backup_path = create_db_backup()
                    if backup_path:
                        prune_old_backups(keep_count=5)
                except Exception as backup_err:
                    logger.warning(f"Failed to create automated backup after sync: {backup_err}")

    async def _run_sync_internal_impl(
        self,
        session: Session,
        start_date: date,
        end_date: date,
        options: dict,
        progress_callback=None
    ) -> dict:
        """
        Execute sync workflow date-by-date and stage-by-stage.
        
        Args:
            session: SQLAlchemy DB Session
            start_date: Start date of price download
            end_date: End date of price download
            options: Dict of stages to run, e.g. {"stocks": True, "corporate_actions": True, ...}
            progress_callback: Optional callable(stage: str, percentage: float, message: str)
            
        Returns:
            Dict containing sync metrics and status.
        """
        self._cancel_requested = False
        summary = {"status": "SUCCESS", "message": "", "records_processed": 0}
        failed_stages = []
        error_details = []
        
        # Determine total trading days in range
        delta = end_date - start_date
        total_days = delta.days + 1
        trading_dates = [start_date + timedelta(days=i) for i in range(total_days)]
        
        # 1. PRE-DOWNLOAD ETF MASTER LIST (needed to filter ETFs from Bhavcopy)
        if self._cancel_requested:
            return self._cancel_summary()
            
        if options.get("etfs") or options.get("stocks"):
            if progress_callback:
                progress_callback("MASTER_DATA", 5.0, "Syncing ETF Master List...")
            try:
                await self.etf_downloader.sync_etf_master_list(session)
            except Exception as e:
                logger.error(f"Failed to sync ETF master list: {e}")
                failed_stages.append("ETF_MASTER")
                error_details.append(f"ETF Master: {e}")

        # Fetch active ETF set for bhavcopy partitioning
        etf_symbols = await self.etf_downloader.get_all_etf_symbols(session)
        if (options.get("etfs") or options.get("stocks")) and not etf_symbols:
            raise ValueError("ETF Master list is empty. Cannot partition Bhavcopy. Sync aborted to prevent data corruption.")

        # Log start of sync log
        sync_type = "FULL_SYNC" if len(options) > 3 else "PARTIAL_SYNC"
        existing_log = session.query(SyncLog).filter_by(
            sync_type=sync_type,
            sync_date=end_date
        ).first()

        if existing_log:
            sync_log = existing_log
            sync_log.status = "STARTED"
            sync_log.started_at = datetime.now()
            sync_log.completed_at = None
            sync_log.error_message = None
            sync_log.records_processed = 0
        else:
            sync_log = SyncLog(
                sync_type=sync_type,
                sync_date=end_date,
                status="STARTED",
                started_at=datetime.now()
            )
            session.add(sync_log)
        session.commit()

        total_prices_imported = 0
        
        try:
            # 2. DATE-BY-DATE INGESTION
            for idx, current_date in enumerate(trading_dates):
                if self._cancel_requested:
                    sync_log.status = "FAILED"
                    sync_log.error_message = "Cancel requested by user"
                    sync_log.completed_at = datetime.now()
                    session.commit()
                    return self._cancel_summary()
                    
                percent = 10.0 + (idx / len(trading_dates)) * 40.0 # Map daily sync to 10% - 50% progress
                
                # Format display date
                disp_date = current_date.strftime("%d-%b-%Y")
                if progress_callback:
                    progress_callback("BHAVCOPY", percent, f"Syncing Daily Data for {disp_date}...")


                # A. Download Daily Bhavcopy once
                has_bhavcopy = False
                bhavcopy_df = None
                
                if options.get("stocks") or options.get("etfs"):
                    date_str = current_date.strftime("%Y%m%d")
                    try:
                        bhavcopy_df = await self.client.download_bhavcopy_csv(date_str)
                        has_bhavcopy = True
                    except HttpNotFoundError:
                        logger.warning(f"Bhavcopy 404 for {current_date.isoformat()} (Weekend/Holiday/Delayed). Skipping.")
                    except HttpStatusError as e:
                        logger.error(f"HTTP error downloading bhavcopy for {current_date.isoformat()}: {e}")
                        raise e
                    except Exception as e:
                        logger.error(f"Error downloading bhavcopy for {current_date.isoformat()}: {e}")
                        raise e

                # B. Ingest regular stocks from bhavcopy
                if options.get("stocks") and has_bhavcopy:
                    try:
                        filtered_stocks = self.stock_downloader.filter_stock_dataframe(bhavcopy_df, etf_symbols)
                        imported = await self.stock_downloader.import_stock_prices(session, filtered_stocks, current_date)
                        total_prices_imported += imported
                    except Exception as e:
                        logger.error(f"Failed to import stocks on {current_date.isoformat()}: {e}")
                        raise e

                # C. Ingest ETFs from bhavcopy
                if options.get("etfs") and has_bhavcopy:
                    try:
                        filtered_etfs = self.etf_downloader.filter_etf_dataframe(bhavcopy_df, etf_symbols)
                        imported = await self.etf_downloader.import_etf_prices(session, filtered_etfs, current_date)
                        total_prices_imported += imported
                    except Exception as e:
                        logger.error(f"Failed to import ETFs on {current_date.isoformat()}: {e}")
                        raise e

                # D. Ingest Indexes
                if options.get("indexes"):
                    try:
                        imported = await self.index_downloader.download_and_import_date(session, current_date)
                        total_prices_imported += imported
                    except Exception as e:
                        # Let the exception propagate to abort the sync if it is a real failure (like today's 404)
                        raise e

            # 3. MASTER DATA ENRICHMENT (Stocks)
            if self._cancel_requested: return self._cancel_summary()
            if options.get("stocks"):
                if progress_callback:
                    progress_callback("MASTER_DATA", 55.0, "Enriching stock profiles from master list...")
                try:
                    df_equity = await self.client.download_equity_list()
                    # Query existing stocks
                    stocks = session.query(Security).filter(Security.security_type == 'STOCK').all()
                    stock_map = {s.symbol: s for s in stocks}
                    isin_map = {s.isin: s for s in stocks if s.isin}
                    
                    enriched_count = 0
                    delisted_count = 0
                    
                    # Dynamically resolve ISIN column (NSE changed from 'ISIN NO' to 'ISIN NUMBER')
                    isin_col = "ISIN NUMBER" if "ISIN NUMBER" in df_equity.columns else "ISIN NO"
                    
                    # Match and update profiles
                    for _, row in df_equity.iterrows():
                        sym = str(row["SYMBOL"]).strip()
                        name = str(row["NAME OF COMPANY"]).strip()
                        isin = str(row[isin_col]).strip()
                        series = str(row["SERIES"]).strip()
                        
                        sec = isin_map.get(isin) or stock_map.get(sym)
                        if sec:
                            sec.company_name = name
                            sec.isin = isin
                            sec.data_source = "MASTER_LIST"
                            sec.is_active = True
                            sec.is_delisted = False
                            enriched_count += 1
                            
                    # Mark stocks NOT in EQUITY_L as delisted
                    master_isins = set(df_equity[isin_col].astype(str).str.strip())
                    for s in stocks:
                        if s.isin and s.isin not in master_isins:
                            s.is_active = False
                            s.is_delisted = True
                            delisted_count += 1
                            
                    session.commit()
                    logger.info(f"Enriched {enriched_count} stocks. Marked {delisted_count} as delisted.")
                except Exception as e:
                    logger.error(f"Failed stock master data enrichment: {e}")
                    failed_stages.append("MASTER_DATA")
                    error_details.append(f"Master Data: {e}")

            # 4. SYMBOL CHANGES
            if self._cancel_requested: return self._cancel_summary()
            if options.get("stocks"):
                if progress_callback:
                    progress_callback("SYMBOL_CHANGES", 60.0, "Syncing ticker rename logs from NSE...")
                try:
                    await self.sc_service.sync_symbol_changes(session)
                except Exception as e:
                    logger.error(f"Failed to sync symbol changes: {e}")
                    failed_stages.append("SYMBOL_CHANGES")
                    error_details.append(f"Symbol Changes: {e}")

            # SYNC TYPE DETECTION: Determine if this is an incremental or full sync.
            # Rule: If the database already has any price history → incremental (process only
            # the new date range, regardless of how many days it spans — even 90 days).
            # If the database is empty/fresh → full (global recalculation of everything).
            # This replaces the old heuristic that was capped at 10 days, which caused
            # a full global recalculation for any catch-up longer than 10 days.
            has_history = session.query(RawPrice).first() is not None
            is_incremental = has_history
            logger.info(
                f"Sync type detection: has_history={has_history}, "
                f"range_days={(end_date - start_date).days}, "
                f"mode={'INCREMENTAL' if is_incremental else 'FULL (fresh database)'}"
            )

            # Detect gaps from previous failed runs and store affected security IDs
            missing_adj_sec_ids = set()
            missing_ind_sec_ids = set()
            if is_incremental:
                missing_adj_sec_ids = self._find_securities_with_adj_gaps(session)
                missing_ind_sec_ids = self._find_securities_with_ind_gaps(session)
                if missing_adj_sec_ids or missing_ind_sec_ids:
                    logger.warning(
                        f"Detected post-processing gaps from a previous run! "
                        f"Missing adjusted prices for {len(missing_adj_sec_ids)} securities. "
                        f"Missing indicators for {len(missing_ind_sec_ids)} securities. "
                        f"Healing these specific securities in incremental run..."
                    )
                else:
                    logger.info("No post-processing gaps detected.")

            # 5. SYNC CORPORATE ACTIONS (must run BEFORE shares fetch so incremental can query actions)
            if self._cancel_requested: return self._cancel_summary()
            if options.get("corporate_actions"):
                if progress_callback:
                    progress_callback("CORPORATE_ACTIONS", 65.0, "Fetching corporate splits and bonuses...")
                try:
                    # Sync actions from start_date to end_date
                    await self.ca_service.sync_corporate_actions(session, start_date, end_date)
                    
                    # If this is an incremental sync, adjust outstanding shares for new corporate actions on their ex-date
                    if is_incremental:
                        from src.models import CorporateAction
                        unprocessed_actions = session.query(CorporateAction).filter(
                            CorporateAction.action_type.in_(["SPLIT", "BONUS"]),
                            CorporateAction.ex_date <= end_date,
                            CorporateAction.is_processed == False
                        ).all()
                        
                        for action in unprocessed_actions:
                            sec = session.get(Security, action.security_id)
                            if sec and sec.issued_shares is not None:
                                old_shares = sec.issued_shares
                                new_shares = int(round(old_shares * float(action.adjustment_factor)))
                                logger.info(f"Adjusting issued_shares for {sec.symbol} due to {action.action_type} (ex-date: {action.ex_date}): {old_shares} -> {new_shares}")
                                sec.issued_shares = new_shares
                            action.is_processed = True
                            action.processed_at = datetime.now()
                        session.commit()
                except Exception as e:
                    logger.error(f"Failed to sync corporate actions: {e}")
                    failed_stages.append("CORPORATE_ACTIONS")
                    error_details.append(f"Corporate Actions: {e}")

            # 6. FETCH SHARES OUTSTANDING (stocks only — after corporate actions so incremental can query them)
            if self._cancel_requested: return self._cancel_summary()
            if options.get("market_cap"):
                if progress_callback:
                    progress_callback("SHARES_OUTSTANDING", 70.0, "Fetching outstanding shares from quote API...")
                try:
                    if options.get("force_shares_refresh"):
                        # FORCED REFRESH: Fetch and overwrite shares for ALL active stocks
                        await self._fetch_shares_for_all_stocks(session, progress_callback, force_refresh=True)
                    elif is_incremental:
                        # INCREMENTAL SYNC: Only re-fetch shares for stocks with corporate actions in this range
                        await self._fetch_shares_for_corporate_action_stocks(
                            session, start_date, end_date, progress_callback
                        )
                    else:
                        # HISTORICAL / FULL SYNC: Fetch shares for ALL stocks with NULL issued_shares
                        await self._fetch_shares_for_all_stocks(session, progress_callback)
                except Exception as e:
                    logger.error(f"Failed to fetch outstanding shares: {e}")
                    failed_stages.append("SHARES_OUTSTANDING")
                    error_details.append(f"Shares Outstanding: {e}")

            # 7. PRICE ADJUSTMENT
            if self._cancel_requested: return self._cancel_summary()
            if options.get("corporate_actions"):
                if progress_callback:
                    progress_callback("PRICE_ADJUSTMENT", 80.0, "Running price adjustment calculations...")
                try:
                    if is_incremental:
                        logger.info("Running optimized incremental price adjustment...")
                        await adjust_incremental_prices(session, start_date, end_date)
                        if missing_adj_sec_ids:
                            logger.info(f"Healing price adjustment gaps for {len(missing_adj_sec_ids)} securities...")
                            for sec_id in missing_adj_sec_ids:
                                await adjust_prices_for_security(session, sec_id)
                                await asyncio.sleep(0.01)
                    else:
                        logger.info("Running global price adjustment...")
                        await adjust_all_prices(session)
                except Exception as e:
                    logger.error(f"Failed price adjustments: {e}")
                    failed_stages.append("PRICE_ADJUSTMENT")
                    error_details.append(f"Price Adjustment: {e}")

            # 8. HISTORICAL MARKET CAP
            if self._cancel_requested: return self._cancel_summary()
            if options.get("market_cap"):
                if progress_callback:
                    progress_callback("MARKET_CAP", 85.0, "Calculating historical market caps...")
                try:
                    if is_incremental:
                        logger.info("Running optimized incremental market cap calculations...")
                        await calculate_incremental_market_caps_for_range(session, start_date, end_date)
                        if missing_adj_sec_ids:
                            logger.info(f"Healing historical market caps for {len(missing_adj_sec_ids)} securities...")
                            for sec_id in missing_adj_sec_ids:
                                sec = session.get(Security, sec_id)
                                if sec and sec.security_type == "STOCK" and sec.issued_shares:
                                    await calculate_historical_market_cap(session, sec_id, sec.issued_shares)
                                    await asyncio.sleep(0.01)
                    else:
                        logger.info("Running global historical market cap calculations...")
                        await calculate_all_historical_market_caps(session)
                except Exception as e:
                    logger.error(f"Failed historical market cap calculations: {e}")
                    failed_stages.append("MARKET_CAP")
                    error_details.append(f"Market Cap: {e}")

            # 9. TECHNICAL INDICATORS
            if self._cancel_requested: return self._cancel_summary()
            if options.get("indicators"):
                if progress_callback:
                    progress_callback("INDICATORS", 90.0, "Calculating SMA technical indicators...")
                try:
                    if is_incremental:
                        logger.info("Running optimized incremental SMA indicator calculations...")
                        await calculate_incremental_indicators_for_range(session, start_date, end_date)
                        
                        affected_ind_sec_ids = missing_ind_sec_ids.union(missing_adj_sec_ids)
                        if affected_ind_sec_ids:
                            logger.info(f"Healing technical indicators for {len(affected_ind_sec_ids)} securities...")
                            for sec_id in affected_ind_sec_ids:
                                await calculate_indicators_for_security(session, sec_id)
                                await asyncio.sleep(0.01)
                    else:
                        logger.info("Running global SMA indicator calculations...")
                        await calculate_all_indicators(session)
                except Exception as e:
                    logger.error(f"Failed technical indicator calculations: {e}")
                    failed_stages.append("INDICATORS")
                    error_details.append(f"Indicators: {e}")

            # Finalize
            if progress_callback:
                progress_callback("FINAL", 100.0, "Sync successfully completed!")
                
            if failed_stages:
                sync_log.status = "PARTIAL"
                sync_log.error_message = f"Failed stages: {', '.join(failed_stages)}. Details: {'; '.join(error_details)}"
                summary["status"] = "PARTIAL"
                summary["message"] = f"Ingested {total_prices_imported} price records, but some stages failed: {', '.join(failed_stages)}"
            else:
                sync_log.status = "SUCCESS"
                summary["status"] = "SUCCESS"
                summary["message"] = f"Ingested {total_prices_imported} price records successfully."
                
            sync_log.records_processed = total_prices_imported
            sync_log.completed_at = datetime.now()
            session.commit()
            
        except Exception as main_err:
            logger.error(f"Sync process aborted due to fatal error: {main_err}")
            sync_log.status = "FAILED"
            sync_log.error_message = str(main_err)
            sync_log.completed_at = datetime.now()
            session.commit()
            raise main_err

        summary["records_processed"] = total_prices_imported
        return summary

    # ========== SHARES OUTSTANDING HELPER METHODS ==========

    async def _fetch_issued_shares_batch(
        self, session: Session, stocks: list, progress_callback=None,
        progress_start: float = 65.0, progress_end: float = 75.0, label: str = ""
    ) -> tuple[int, int, list]:
        """
        Fetch issued shares for a list of stocks in batches of 50, using BSEClient to avoid NSE blocks.
        """
        from src.services.bse_client import BSEClient
        
        BATCH_SIZE = 50
        BATCH_PAUSE_SECONDS = 5.0
        SUCCESS_DELAY_SECONDS = 0.5
        FAILURE_DELAY_SECONDS = 1.0
        MAX_CONSECUTIVE_FAILURES = 15
        COOLDOWN_THRESHOLD = 5
        COOLDOWN_AFTER_STREAK = 10.0

        total = len(stocks)
        if total == 0:
            return 0, 0, []

        success_count = 0
        failure_count = 0
        consecutive_failures = 0
        failed_stocks_list = []

        bse_client = BSEClient()
        try:
            logger.info(f"{label}Fetching issued shares via BSE for {total} stocks in batches of {BATCH_SIZE}...")

            for batch_idx in range(0, total, BATCH_SIZE):
                if self._cancel_requested:
                    break

                batch = stocks[batch_idx:batch_idx + BATCH_SIZE]
                batch_num = (batch_idx // BATCH_SIZE) + 1
                total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

                logger.info(f"{label}Starting batch {batch_num}/{total_batches} ({len(batch)} stocks)...")

                for i, stock in enumerate(batch):
                    if self._cancel_requested:
                        break

                    global_idx = batch_idx + i
                    pct = progress_start + (global_idx / total) * (progress_end - progress_start)
                    if progress_callback:
                        progress_callback(
                            "SHARES_OUTSTANDING", pct,
                            f"{label}Batch {batch_num}/{total_batches} — {stock.symbol} ({global_idx+1}/{total})..."
                        )

                    try:
                        if not stock.isin:
                            logger.warning(f"Stock {stock.symbol} has no ISIN. Skipping BSE lookup.")
                            failure_count += 1
                            consecutive_failures += 1
                            failed_stocks_list.append(stock)
                            continue

                        scrip_code = await bse_client.lookup_scripcode_by_isin(stock.isin)
                        if not scrip_code:
                            logger.warning(f"Could not resolve BSE scripcode for {stock.symbol} (ISIN: {stock.isin})")
                            failure_count += 1
                            consecutive_failures += 1
                            failed_stocks_list.append(stock)
                            await asyncio.sleep(FAILURE_DELAY_SECONDS)
                        else:
                            issued, qtr_date = await bse_client.fetch_outstanding_shares(scrip_code)
                            if issued and issued > 0:
                                stock.issued_shares = issued
                                if qtr_date:
                                    from src.models import HistoricalShare
                                    from sqlalchemy import select
                                    stmt = select(HistoricalShare).where(
                                        HistoricalShare.security_id == stock.id,
                                        HistoricalShare.quarter_date == qtr_date
                                    )
                                    existing = session.execute(stmt).scalar()
                                    if existing:
                                        existing.issued_shares = issued
                                    else:
                                        new_share = HistoricalShare(
                                            security_id=stock.id,
                                            quarter_date=qtr_date,
                                            issued_shares=issued,
                                            source="BSE_QUARTERLY_SHP"
                                        )
                                        session.add(new_share)
                                    
                                    # Backfill historical quarterly shares for this stock from 2024-01-01
                                    try:
                                        from src.services.historical_shares import sync_historical_shares_for_security
                                        from datetime import date as dt
                                        await sync_historical_shares_for_security(
                                            session, stock.id, scrip_code, dt(2024, 1, 1), bse_client
                                        )
                                    except Exception as hist_sync_err:
                                        logger.warning(f"Failed to sync historical shares for {stock.symbol}: {hist_sync_err}")
                                
                                session.commit()
                                success_count += 1
                                consecutive_failures = 0
                                logger.info(f"Updated shares outstanding for {stock.symbol} (BSE: {scrip_code}): {issued:,}")
                                await asyncio.sleep(SUCCESS_DELAY_SECONDS)
                            else:
                                logger.warning(f"Failed to fetch/parse outstanding shares from BSE for {stock.symbol} (Scrip: {scrip_code})")
                                failure_count += 1
                                consecutive_failures += 1
                                failed_stocks_list.append(stock)
                                await asyncio.sleep(FAILURE_DELAY_SECONDS)

                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            logger.error(
                                f"{label}Too many consecutive failures ({consecutive_failures}). "
                                f"Aborting BSE shares fetch stage."
                            )
                            return success_count, failure_count, failed_stocks_list

                        if consecutive_failures >= COOLDOWN_THRESHOLD:
                            logger.warning(
                                f"{label}{consecutive_failures} consecutive failures — "
                                f"entering {COOLDOWN_AFTER_STREAK}s cooldown..."
                            )
                            await asyncio.sleep(COOLDOWN_AFTER_STREAK)

                    except Exception as e:
                        logger.warning(f"Error fetching BSE shares for {stock.symbol}: {e}")
                        failure_count += 1
                        consecutive_failures += 1
                        failed_stocks_list.append(stock)
                        
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            logger.error(
                                f"{label}Too many consecutive failures ({consecutive_failures}). "
                                f"Aborting BSE shares fetch stage."
                            )
                            return success_count, failure_count, failed_stocks_list

                        await asyncio.sleep(FAILURE_DELAY_SECONDS)

                # Pause between batches (except after the last one)
                if batch_idx + BATCH_SIZE < total and not self._cancel_requested:
                    logger.info(
                        f"{label}Batch {batch_num}/{total_batches} complete "
                        f"(success: {success_count}, failures: {failure_count}). "
                        f"Pausing {BATCH_PAUSE_SECONDS}s before next batch..."
                    )
                    await asyncio.sleep(BATCH_PAUSE_SECONDS)
        finally:
            await bse_client.close()

        logger.info(
            f"{label}BSE Shares outstanding fetch completed. "
            f"Success: {success_count}, Failures: {failure_count}, Total: {total}"
        )
        return success_count, failure_count, failed_stocks_list

    async def _fetch_shares_for_all_stocks(self, session: Session, progress_callback=None, force_refresh: bool = False):
        """
        HISTORICAL / FULL SYNC: Fetch issued shares for ALL active stocks.
        If force_refresh is False, only fetches for stocks that have NULL issued_shares.
        Uses batched fetching with pauses to avoid rate limiting.
        """
        query = session.query(Security).filter(
            Security.security_type == 'STOCK',
            Security.is_active == True,
            Security.is_delisted == False
        )
        if not force_refresh:
            query = query.filter(Security.issued_shares == None)
            
        missing_shares_stocks = query.all()

        if not missing_shares_stocks:
            logger.info("[FULL] All active stocks already have issued_shares populated.")
            return

        success, failures, failed_stocks = await self._fetch_issued_shares_batch(
            session, missing_shares_stocks, progress_callback,
            progress_start=65.0, progress_end=75.0, label="[FULL] " if not force_refresh else "[REFRESH] "
        )

        if failures > 0 and not self._cancel_requested:
            if failed_stocks:
                logger.info(f"[FULL] Retrying {len(failed_stocks)} failed OS shares downloads once more...")
                await self._fetch_issued_shares_batch(
                    session, failed_stocks, progress_callback,
                    progress_start=75.0, progress_end=78.0, label="[FULL RETRY] "
                )

    async def _fetch_shares_for_corporate_action_stocks(
        self, session: Session, start_date: date, end_date: date, progress_callback=None
    ):
        """
        INCREMENTAL SYNC: Only re-fetch issued shares for stocks that had corporate actions
        (splits/bonuses) with ex-date in the sync date range.
        These events change the outstanding share count, so we need fresh data from NSE.
        """
        from src.models import CorporateAction

        # Find stocks with SPLIT or BONUS actions ex-dating in this range
        affected_ids = set(
            session.query(CorporateAction.security_id).filter(
                CorporateAction.action_type.in_(["SPLIT", "BONUS"]),
                CorporateAction.ex_date >= start_date,
                CorporateAction.ex_date <= end_date
            ).distinct().all()
        )
        # Flatten the tuples
        affected_ids = {row[0] for row in affected_ids}

        if not affected_ids:
            logger.info("[INCR] No corporate actions in date range — skipping shares re-fetch.")
            return

        # Fetch the Security objects for affected stocks
        affected_stocks = session.query(Security).filter(
            Security.id.in_(affected_ids),
            Security.security_type == 'STOCK',
            Security.is_active == True,
            Security.is_delisted == False
        ).all()

        if not affected_stocks:
            logger.info("[INCR] No active stocks matched corporate action IDs.")
            return

        logger.info(
            f"[INCR] Re-fetching issued shares for {len(affected_stocks)} stocks "
            f"with corporate actions (ex-date: {start_date} to {end_date})..."
        )

        success, failures, failed_stocks = await self._fetch_issued_shares_batch(
            session, affected_stocks, progress_callback,
            progress_start=65.0, progress_end=70.0, label="[INCR] "
        )

        if failures > 0 and not self._cancel_requested:
            if failed_stocks:
                logger.info(f"[INCR] Retrying {len(failed_stocks)} failed OS shares downloads once more...")
                await self._fetch_issued_shares_batch(
                    session, failed_stocks, progress_callback,
                    progress_start=70.0, progress_end=72.0, label="[INCR RETRY] "
                )

    def _cancel_summary(self) -> dict:
        logger.warning("Data sync cancelled by user request.")
        return {
            "status": "CANCELLED",
            "message": "The synchronization job was cancelled by user.",
            "records_processed": 0
        }

