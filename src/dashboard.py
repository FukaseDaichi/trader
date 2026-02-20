import json
import shutil
import pandas as pd
import numpy as np
from datetime import datetime
from .config import BASE_DIR, DOCS_DIR, STATE_FILE, TICKERS
from .data_loader import load_data
from .model import add_features

def update_dashboard(signals):
    """
    Update state.json and generate history_data.json
    """
    # 1. Update State (History of Signals)
    update_state(signals)
    
    # 2. Generate Full History Data for Dashboard
    export_history_data()

def update_state(signals):
    """
    Update state.json with new signals
    """
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
    else:
        state = {"history": [], "last_update": ""}
    
    today_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    state['last_update'] = today_str
    
    days_entry = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "signals": signals
    }
    
    state['history'].insert(0, days_entry)
    state['history'] = state['history'][:30] # Keep last 30
    
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def export_history_data():
    """
    Load data for all enabled tickers, calculate features, and save to json
    """
    history_data = {}
    
    # Load signal history to map checks
    state = {}
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
            
    history_data["last_update"] = state.get("last_update", "")
    history_data["tickers"] = {}
    
    for ticker_info in TICKERS:
        code = ticker_info['code']
        name = ticker_info['name']
        
        df = load_data(code)
        if df is None or df.empty:
            continue
            
        # Add features without dropping NaNs to keep price history
        df = add_features(df, dropna=False)
        
        # Convert date to string
        df['date'] = df['date'].dt.strftime('%Y-%m-%d')
        
        # Replace NaN with None (null in JSON)
        df = df.replace({np.nan: None})
        
        # Convert to list of dicts
        records = df.to_dict(orient='records')
        
        history_data["tickers"][code] = {
            "name": name,
            "data": records
        }

    # Include recent signals
    history_data["signals_history"] = state.get("history", [])

    output_file = DOCS_DIR / "history_data.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(history_data, f, ensure_ascii=False) # remove indent to save space, or indent=2 for debug
        
    print(f"History data exported to {output_file}")

    # Also copy to web/public/ for local development (npm run dev)
    dev_public_dir = BASE_DIR / "web" / "public"
    if dev_public_dir.exists():
        shutil.copy2(output_file, dev_public_dir / "history_data.json")
