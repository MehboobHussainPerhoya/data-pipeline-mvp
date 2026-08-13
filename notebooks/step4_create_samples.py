import pandas as pd
import requests
import json
import os

os.makedirs("data/sample/support_tickets", exist_ok=True)
os.makedirs("data/sample/kb_articles", exist_ok=True)
os.makedirs("data/sample/support_activity_api", exist_ok=True)

# Support tickets sample
tickets = pd.read_csv("data/raw/support_tickets/customer_support_tickets.csv")
tickets.head(200).to_csv("data/sample/support_tickets/sample.csv", index=False)

# KB articles sample
kb = pd.read_csv("data/raw/kb_articles/bitext_customer_support.csv")
kb.head(200).to_csv("data/sample/kb_articles/sample.csv", index=False)

# API sample
resp = requests.get("https://jsonplaceholder.typicode.com/todos")
data = resp.json()[:200]
with open("data/sample/support_activity_api/sample.json", "w") as f:
    json.dump(data, f, indent=2)

print("Samples created:")
print(" - data/sample/support_tickets/sample.csv:", len(tickets.head(200)), "rows")
print(" - data/sample/kb_articles/sample.csv:", len(kb.head(200)), "rows")
print(" - data/sample/support_activity_api/sample.json:", len(data), "records")