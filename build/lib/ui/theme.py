from nicegui import ui


def apply_theme():
    """Apply custom dark theme with premium aesthetics."""

    ui.add_head_html('''
<style>
:root {
    --primary: #6366f1;        /* Indigo */
    --primary-light: #818cf8;
    --primary-dark: #4f46e5;
    --accent: #22d3ee;         /* Cyan */
    --success: #10b981;        /* Emerald */
    --warning: #f59e0b;        /* Amber */
    --danger: #ef4444;         /* Red */
    --surface: #1e1e2e;        /* Dark surface */
    --surface-light: #2a2a3e;
    --text-primary: #e2e8f0;
    --text-secondary: #94a3b8;
    --gain: #10b981;           /* Green for positive */
    --loss: #ef4444;           /* Red for negative */
}

body {
    font-family: 'Inter', 'Segoe UI', sans-serif;
    background: var(--surface);
    color: var(--text-primary);
}

/* Glassmorphism card effect */
.glass-card {
    background: rgba(30, 30, 46, 0.8);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 20px;
}

/* Stat card styling */
.stat-card {
    background: linear-gradient(135deg, var(--surface-light), var(--surface));
    border-radius: 16px;
    padding: 24px;
    border: 1px solid rgba(255, 255, 255, 0.06);
    transition: transform 0.2s, box-shadow 0.2s;
}
.stat-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
}

/* Sidebar styling */
.sidebar-item {
    border-radius: 8px;
    transition: background 0.2s;
}
.sidebar-item:hover {
    background: rgba(99, 102, 241, 0.15);
}
.sidebar-item.active {
    background: rgba(99, 102, 241, 0.25);
    border-left: 3px solid var(--primary);
}
</style>

<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
''', shared=True)
