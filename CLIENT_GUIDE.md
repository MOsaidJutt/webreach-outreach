# WebReach — Client User Guide
### Automated Website Outreach System

**Live Dashboard:** https://outreach.sms2cartdemo.com

---

## What This System Does

WebReach automatically finds local businesses on Google Maps that have great reviews but **no website**, then contacts them via SMS with an AI-powered conversation agent. When a business shows interest, you are flagged to manually create their website and send them the link.

**The full flow:**
```
Scrape Google Maps → Filter leads → Import to GHL → AI sends SMS
→ AI handles replies → Interested leads flagged → You send website → Conversion
```

---

## Step 1 — Scraping Leads

### Option A: API Search (Quick)
1. Go to **Scraping Jobs** in the sidebar
2. Click the **API Search** tab
3. Enter a business type (e.g. `restaurants`, `hair salons`, `plumbers`)
4. Enter a location (e.g. `Bristol UK`, `Miami FL`)
5. Click **Start Scraping**
6. A progress dialog appears — results load in 1–3 minutes
7. Click **View Leads** when complete

### Option B: Outscraper CSV Import (Recommended — More Accurate)
This method uses Outscraper's native "no website" filter for better results.

1. Go to **app.outscraper.com** and log in
2. Click **Google Maps** search
3. Enter your search query and location
4. Under **Filters**, set:
   - Has website → **No**
   - Rating → **≥ 4.0**
   - Has phone → **Yes**
5. Run the search and wait for results
6. Click **Export → CSV**
7. In WebReach → **Scraping Jobs** → **Import from Outscraper CSV** tab
8. Upload the CSV file
9. Click **Import Leads**

---

## Step 2 — Filtering Your Leads

Once leads are imported, go to the **Leads** page. Use the filter bar to narrow down:

| Filter | Options |
|--------|---------|
| 📱 Phone | Has Phone / No Phone |
| 🌐 Website | No Website / Has Website |
| ⭐ Rating | 3.0+ / 4.0+ / 4.5+ / 5 only |
| Status | Not Contacted / Sent / Replied / Interested / etc. |
| Search | Name, phone, city, category |

**Recommended filter for outreach:**
- Phone: **Has Phone**
- Website: **No Website**
- Rating: **4.0+**

This gives you the ideal target list — businesses with good ratings, no website, and a contactable phone number.

---

## Step 3 — Import Leads to GHL

GHL (Go High Level) is where contacts are managed and SMS messages are sent.

**Import all at once:**
- Dashboard → click **Import All to GHL**

**Import individually:**
- Leads page → click the ☁️ icon next to any lead

---

## Step 4 — Send SMS Outreach

**Send to all eligible leads at once:**
- Dashboard → click **Send Bulk SMS**

**Send to one lead:**
- Leads page → click the ✉️ send icon next to a lead

The AI agent takes over from here and handles all replies automatically.

---

## Step 5 — The AI Conversation Flow

Once the SMS is sent, the AI handles the full conversation:

| Step | Message |
|------|---------|
| **1** | "Hi! Is this [Business Name]? 👋 I'm Sarah from AMZUS Digital." |
| **2** | Compliments their Google rating, points out they have no website |
| **3** | Offers a free website preview — "Would you like me to send the link?" |
| **4** | If they say **yes** → flagged as Interested, you are notified |
| — | If they say **no** → politely closed |
| — | If they say **stop** → removed from list, never contacted again |

**If a lead asks "what's your website?"** — the AI automatically replies with your website URL.

---

## Step 6 — Managing Interested Leads

When a lead says yes, their status changes to **Interested** and they are tagged in GHL.

To view all interested leads:
1. Go to **Leads**
2. Filter by Status → **Interested**

For each interested lead:
1. Create their website on **Lovable** (lovable.dev)
2. Come back to WebReach → click the pencil ✏️ icon on the lead
3. Change status to **Website Sent**
4. Paste the Lovable URL in the Website URL field
5. Save
6. Send the link to the lead manually via GHL

When they convert to a paying customer, update status to **Converted**.

---

## Step 7 — Auto Follow-Up

The system automatically follows up with leads who haven't replied.

**Default settings:**
- Wait **3 days** before following up
- Send maximum **2 follow-up messages**
- Stops automatically after max attempts

**To change these settings:**
1. Go to **Admin** in the sidebar
2. Adjust Follow-Up Interval and Max Follow-Ups
3. Click **Save Settings**

**To trigger follow-ups manually right now:**
- Admin → click **Run Now**

---

## Step 8 — Analytics Dashboard

The Dashboard shows your key metrics at a glance:

| Metric | What it means |
|--------|--------------|
| Total Businesses | All leads scraped |
| Messages Sent | Leads contacted via SMS |
| Interested Leads | Said yes to website offer |
| Converted | Became paying customers |
| Response Rate | % of leads that replied |
| Interest Rate | % of replies that were interested |
| Conversion Rate | % of interested leads that converted |

---

## Pipeline Status Reference

| Status | Meaning |
|--------|---------|
| 🔘 Not Contacted | Lead scraped, no message sent yet |
| 🔵 Message Sent | Initial greeting SMS sent |
| 🟦 Replied | Lead replied, AI conversation in progress |
| 🟡 Interested | Said yes — **needs your attention** |
| 🔴 Not Interested | Declined the offer |
| ⚫ Opted Out | Asked to stop — do not contact |
| 🟢 Website Sent | You sent them the Lovable website link |
| 🟢 Converted | Paying customer 🎉 |

---

## Exporting Data

Export your leads at any time as CSV or Excel:

- **Top right of any page** → Export CSV / Export Excel
- **Leads page** → Export button (exports only the current filtered view)

This means you can export just your **Interested** leads, just **Converted** customers, etc.

---

## Testing the AI Conversation

Before running a real campaign, test how the AI will respond:

1. Go to **AI Chat Test** in the sidebar
2. Enter a test business name, rating, and review count
3. Click **Start New Conversation**
4. Reply as if you were the business owner
5. Try different responses: "yes", "no", "stop", "what's your website?"

This lets you see exactly what your leads will experience.

---

## Admin Settings

Go to **Admin** to configure:

| Setting | What it does |
|---------|-------------|
| Enable Follow-Ups | Turn auto follow-up on/off |
| Follow-Up Interval | Days to wait before following up (default: 3) |
| Max Follow-Ups | How many times to chase a lead (default: 2) |
| Business Name | Your company name used in SMS messages |
| Your Website | Sent when leads ask "who are you?" |
| Agent Name | The name the AI introduces itself with |

---

## Bulk Actions on Leads Page

Select multiple leads using the checkboxes, then:

- **Import Selected to GHL** — import a batch to GHL
- **Send SMS to Selected** — send initial SMS to a batch
- **Delete Selected** — remove leads from the system

Tick the top checkbox to **select all** visible leads at once.

---

## Frequently Asked Questions

**Q: Will leads who opted out ever be messaged again?**
No. Once a lead's status is "Opted Out" the system never messages them again.

**Q: What happens if a lead replies after we've already flagged them as interested?**
The AI stops auto-replying once a lead is at step 3 (interested). All further communication is manual via GHL.

**Q: Can I message the same business twice?**
No. The system deduplicates by Google Place ID and phone number. The same business won't be imported twice.

**Q: How much does Outscraper cost?**
Approximately $2–5 per 1,000 businesses scraped. New accounts get free credits.

**Q: What if a lead has a website but it wasn't detected?**
The "No Website" filter works best when using the Outscraper CSV method (Option B in Step 1), as it uses Outscraper's native server-side filter which is more accurate than API post-filtering.

---

## Support

For any issues with the system, contact your developer.

For GHL issues: **help.gohighlevel.com**
For Outscraper issues: **outscraper.com/support**
