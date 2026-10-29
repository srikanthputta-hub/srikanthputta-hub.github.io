#!/usr/bin/env python3
"""One-time, run locally: python get_refresh_token.py client_secret.json  -> prints YT_REFRESH_TOKEN."""
import sys
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"])
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("YT_REFRESH_TOKEN =", creds.refresh_token)
