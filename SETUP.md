# WebReach — Outreach Automation System
## Complete Setup Guide

---

## What This System Does

1. **Scrapes Google Maps** via Outscraper API to find businesses with 4★+ ratings but no website
2. **Imports leads** to Go High Level (GHL) as contacts automatically
3. **Sends AI-powered SMS** via GHL that follows a multi-step conversation flow
4. **Auto-replies** to incoming SMS with contextual responses based on intent
5. **Flags interested leads** with a "interested-in-website" tag in GHL for manual follow-up
6. **Tracks the full pipeline** in a clean web dashboard

---

## Step 1 — Install Python

You need Python 3.11 or higher.

- Download from: https://python.org/downloads/
- During install, check "Add Python to PATH"

Verify: open a terminal and run `python --version`

---

## Step 2 — Set Up the Project

Open a terminal (Command Prompt or PowerShell) and navigate to the project folder:

```
cd "C:\Users\Hamza Khan\Desktop\Upwork\14 - Nicholas 2"
```

Create a virtual environment:
```
python -m venv venv
venv\Scripts\activate
```

Install dependencies:
```
pip install -r requirements.txt
```

---

## Step 3 — Configure Your API Keys

Copy the example env file:
```
copy .env.example .env
```

Open `.env` in Notepad and fill in each value:

### Outscraper API Key
1. Go to https://app.outscraper.com → Sign in
2. Click your name (top right) → **API Key**
3. Copy the key → paste as `OUTSCRAPER_API_KEY=...`

> Outscraper pricing: approximately $2–5 per 1,000 businesses scraped. New accounts get free credits.

### Go High Level (GHL)

**API Key:**
1. In GHL: Settings → Integrations → **API Keys**
2. Create a new key → copy to `GHL_API_KEY=...`

**Location ID:**
1. In GHL: Settings → **Business Profile**
2. The Location ID is shown in the URL or on the page
3. Copy to `GHL_LOCATION_ID=...`

**SMS From Number:**
1. In GHL: Settings → **Phone Numbers**
2. If you don't have one, purchase a Twilio number through GHL
3. Copy the number (e.g. `+15551234567`) to `SMS_FROM_NUMBER=...`

### OpenAI (Optional)
- Get key from: https://platform.openai.com/api-keys
- Paste to `OPENAI_API_KEY=...`
- This improves intent detection for ambiguous replies (e.g. "maybe later")
- Falls back to rule-based detection if not set

### Webhook Secret
- Generate a random string (e.g. use https://randomkeygen.com)
- Paste to `WEBHOOK_SECRET=...`
- Use the same value when configuring the webhook in GHL

---

## Step 4 — Start the Server

```
python app.py
```

The dashboard will be available at: **http://localhost:5000**

---

## Step 5 — Set Up GHL Webhook (for AI Reply Processing)

The webhook allows GHL to send incoming SMS replies to your server for AI processing.

**For local testing (your PC), use ngrok:**
1. Download ngrok: https://ngrok.com/download
2. Run: `ngrok http 5000`
3. Copy the HTTPS URL (e.g. `https://abc123.ngrok.io`)
4. Your webhook URL will be: `https://abc123.ngrok.io/api/webhooks/ghl`

**For production (VPS/cloud):**
- Your webhook URL will be: `https://yourdomain.com/api/webhooks/ghl`

**Register the webhook in GHL:**
1. Go to GHL → Settings → **Webhooks** (or Integrations → Webhooks)
2. Click **Add Webhook**
3. Enter your webhook URL from above
4. Select event: **InboundMessage**
5. Save

---

## Step 6 — Run Your First Scrape

1. Open the dashboard: http://localhost:5000
2. Click **Scraping Jobs** in the sidebar
3. Enter a business type (e.g. "restaurants") and location (e.g. "Miami FL")
4. Set minimum rating to 4.0★
5. Click **Start Scraping**
6. Wait 1–3 minutes — leads will appear automatically

---

## Step 7 — Import Leads to GHL

From the dashboard:
1. Click **Import All to GHL** (imports all leads with phone numbers)

Or per-lead:
1. Go to **Leads** → click the cloud icon next to any lead

---

## Step 8 — Send SMS Outreach

**Bulk (recommended):**
1. Dashboard → **Send Bulk SMS** — sends to all GHL-imported leads not yet contacted

**Individual:**
1. Leads page → click the send icon next to a lead

---

## Step 9 — Monitor Replies

- Incoming SMS replies are automatically processed by the AI agent
- The conversation flows through 3–4 steps automatically
- When a lead says "yes" to the website offer → they're tagged `interested-in-website` in GHL
- You'll see them under **Leads → Filter: Interested**
- Create their website on **Lovable**, then manually send them the link via GHL

---

## Step 10 — Mark Website as Sent

1. Go to **Leads** → find the interested lead
2. Click the pencil icon → Status: **Website Sent**
3. Paste the Lovable website URL in the Website URL field
4. Click Save

When they convert, update status to **Converted**.

---

## Pipeline Status Reference

| Status | Meaning |
|--------|---------|
| Not Contacted | Lead scraped, no message sent yet |
| Message Sent | Initial greeting SMS sent |
| Replied | Lead replied, AI conversation in progress |
| Interested ⭐ | Lead said yes to website — needs manual follow-up |
| Not Interested | Lead declined |
| Opted Out | Lead asked to stop — do not contact |
| Website Sent | You've sent them the Lovable website link |
| Converted | They became a paying customer 🎉 |

---

## Exporting Data

- **CSV**: Dashboard → Export CSV button (top right), or Leads page → Export button
- **Excel**: Same buttons, select Excel format
- Both exports include all lead data and pipeline status

---

## Running in Production

For a live server (DigitalOcean, AWS, etc.):

```bash
# Install production server
pip install gunicorn

# Run with gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

Use nginx as a reverse proxy and point your domain to port 5000.

---

## Troubleshooting

**"OUTSCRAPER_API_KEY is not configured"**
→ Make sure your `.env` file exists and has the correct key. Restart the server after editing `.env`.

**"GHL API error 401"**
→ Your GHL API key is wrong or expired. Re-generate it in GHL → Settings → API Keys.

**SMS not sending**
→ Verify `GHL_LOCATION_ID`, `SMS_FROM_NUMBER` are correct, and the number is active in GHL.

**Webhook not firing**
→ Make sure ngrok is running, the webhook URL is correct in GHL, and the event type is `InboundMessage`.

**Import fails for some leads**
→ Leads without phone numbers cannot be imported to GHL. These are expected to fail — Outscraper filters most of these out already.

---

## Conversation AI Flow (Summary)

```
You: "Hi! Is this [Business Name]? 👋"
Lead: "Yes!"

You: "You have a great 4.8★ rating! However, you don't have a website
      linked — you could be missing out on sales."
Lead: "Oh really?"

You: "We've built a free preview website for you. Want me to send the link?"
Lead: "Yes please!"

You: "Wonderful! Someone will be in touch shortly. 😊"
→ Lead tagged "interested-in-website" in GHL
→ You create the site on Lovable and send manually
```

Objection handling:
- "No" / "Not interested" → Polite goodbye, status = Not Interested
- "Stop" / "Unsubscribe" → Removed from list, status = Opted Out
- Ambiguous replies → AI asks for clarification (Yes or No?)
