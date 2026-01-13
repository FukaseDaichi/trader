import json
import pandas as pd
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from .config import DOCS_DIR, STATE_FILE, BASE_DIR

def update_dashboard(signals):
    """
    Update state.json and regenerate index.html
    """
    # Load existing state
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
    else:
        state = {"history": [], "last_update": ""}
    
    today_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    state['last_update'] = today_str
    
    # Append new signals to history
    # We want to store history per ticker or just a flat list?
    # Requirement: "Past signals history (last 30 days)"
    # Let's add today's signals to a list
    
    # Filter out redundant data for history to save space if needed, 
    # but keeping it simple is better.
    days_entry = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "signals": signals
    }
    
    # Add to history
    state['history'].insert(0, days_entry)
    
    # Keep last 30 entries
    state['history'] = state['history'][:30]
    
    # Save state
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        
    # Generate HTML
    generate_html(state)

def generate_html(state):
    env = Environment(loader=FileSystemLoader(BASE_DIR / 'src' / 'templates'))
    template = env.get_template('index.html')
    
    # Prepare data for easy rendering
    # Latest signals are in state['history'][0]['signals'] if valid
    latest_signals = state['history'][0]['signals'] if state['history'] else []
    
    html = template.render(
        last_update=state['last_update'],
        latest_signals=latest_signals,
        history=state['history']
    )
    
    with open(DOCS_DIR / 'index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("Dashboard updated.")
