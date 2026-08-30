import requests
import pandas as pd

API_KEY = "20c75ed56b3b413a983ed9d4eac336f7"

url = "https://api.twelvedata.com/time_series"

params = {
    "symbol": "XAU/USD",
    "interval": "1min",
    "outputsize": 500,
    "apikey": API_KEY,
}

response = requests.get(url, params=params)
data = response.json()

df = pd.DataFrame(data["values"])

df["datetime"] = pd.to_datetime(df["datetime"])
df["open"] = df["open"].astype(float)
df["high"] = df["high"].astype(float)
df["low"] = df["low"].astype(float)
df["close"] = df["close"].astype(float)

df = df.sort_values("datetime")

print(df.tail())