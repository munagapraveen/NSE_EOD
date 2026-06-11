import sys
import os

# Append the project's root directory to the python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ui.app import main

if __name__ == "__main__":
    main()
