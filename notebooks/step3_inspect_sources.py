import pandas as pd
import requests

# --- Support tickets ---
tickets = pd.read_csv("data/raw/support_tickets/customer_support_tickets.csv")
print("=== SUPPORT TICKETS ===")
print("Columns:", tickets.columns.tolist())
print(tickets.head(2))
print()

# --- KB articles ---
kb = pd.read_csv("data/raw/kb_articles/bitext_customer_support.csv")
print("=== KB ARTICLES ===")
print("Columns:", kb.columns.tolist())
print(kb.head(2))
print()

# --- GitHub Issues API ---
resp = requests.get("https://jsonplaceholder.typicode.com/todos")
data = resp.json()
print("=== SUPPORT ACTIVITY (API) ===")
print("Keys:", list(data[0].keys()))
print(data[0])