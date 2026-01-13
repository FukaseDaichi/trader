import pandas as pd
import requests
import io
from datetime import datetime
from .config import DATA_DIR

def download_stooq_data(ticker_code):
    """
    Download historical data from Stooq.
    Stooq URL format for CSV download: https://stooq.com/q/d/l/?s={code}&i=d
    """
    url = f"https://stooq.com/q/d/l/?s={ticker_code}&i=d"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        # Check if content is valid CSV (sometimes Stooq returns an HTML page on error)
        if "Preceded by" in response.text: # Typical Stooq error message start
             print(f"Error fetching data for {ticker_code}: Invalid ticker or data not found.")
             return None
             
        df = pd.read_csv(io.StringIO(response.text))
        
        # Stooq columns are usually lower case or mixed, standardize them
        df.columns = [c.lower() for c in df.columns]
        
        # Check required columns
        required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required_cols):
            print(f"Error: Missing columns in Stooq data for {ticker_code}")
            return None

        # Parse date
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        return df
    
    except Exception as e:
        print(f"Failed to download data for {ticker_code}: {e}")
        return None

def update_data(ticker_code):
    """
    Update local parquet file with latest data from Stooq.
    """
    print(f"Updating data for {ticker_code}...")
    
    # Download fresh data
    new_df = download_stooq_data(ticker_code)
    
    if new_df is None or new_df.empty:
        print(f"No new data found for {ticker_code}.")
        return None
    
    file_path = DATA_DIR / f"{ticker_code}.parquet"
    
    if file_path.exists():
        old_df = pd.read_parquet(file_path)
        # Combine and drop duplicates based on Date
        combined_df = pd.concat([old_df, new_df]).drop_duplicates(subset=['date'], keep='last')
        combined_df = combined_df.sort_values('date').reset_index(drop=True)
    else:
        combined_df = new_df
        
    # Save to parquet
    combined_df.to_parquet(file_path)
    print(f"Data saved to {file_path}. Latest date: {combined_df['date'].max().strftime('%Y-%m-%d')}")
    
    return combined_df

def load_data(ticker_code):
    file_path = DATA_DIR / f"{ticker_code}.parquet"
    if file_path.exists():
        return pd.read_parquet(file_path)
    return None
