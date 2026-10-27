# MSK Bot — Daily One-Stock Fundamental PDF (₹0-first)

## उद्देश्य

हर कार्य-दिन शाम **7:40 बजे IST**, बिना रोज़ नया टास्क लिखे, MSK Bot से **केवल एक** NSE शेयर की पूरी PDF रिसर्च रिपोर्ट तय Telegram chat में भेजना। सिर्फ पुराने पैसे/अधूरे data से अनुमानित रिपोर्ट बनाना मना है। कोई paid AI API उपयोग नहीं होगा।

यह रिपोर्ट **सुझाव/BUY call नहीं**, स्रोत-आधारित educational research होगी। Market holiday, Yahoo/Screener unavailable, incomplete quarterly/annual data या PDF quality gate fail होने पर उस दिन पोस्ट **SKIP** होगी, गलत रिपोर्ट नहीं जाएगी।

## मौजूदा PDF की सामग्री

`report_generator.py` की institutional PDF: Summary, Price और Valuation, Revenue/Profit Growth, ROE/ROCE/P/E/PEG, quarterly और annual tables, ownership, peers (जब उपलब्ध), technical charts, scenarios, invalidation levels, financial risks, catalysts और source/disclaimer appendix।

## One-time ऑप्ट-इन (बिना अनुमति चालू नहीं)

1. GitHub repository Actions Secrets में `TELEGRAM_TOKEN` (MSK Bot, न कि AiAutoUpdate Bot का token) और `MSK_DAILY_RESEARCH_CHAT_ID` (पहले **private admin/test chat**) रखें। Key को chat में न लिखें।
2. GitHub repository Actions Variable में `MSK_DAILY_RESEARCH_ENABLED=true` रखें।
3. Repo के Actions को `contents:write` के साथ अलग `msk-daily-pdf-state` branch में प्रगति रखने की अनुमति दें। पहले से बनाई गई state branch में `msk_daily_research_state.json` मौजूद होनी चाहिए।
4. शुरुआत में workflow `workflow_dispatch` से **केवल निजी टेस्ट chat** में जाँचें, GitHub Actions job के output में सफल Telegram message ID और state-branch `SENT` status देखें। बिना actual ACK के successful delivery का दावा नहीं।
5. निजी टेस्ट PASS होने के बाद ही target chat को production distribution chat में बदलने का निर्णय लें।

## कैसे काम करता है

- 30 बड़ी NSE कंपनियों की rotating watchlist से हर weekday अलग शेयर का चुनाव। अधिकतम 4 candidate; पुरानी closing candle, valuation, annual/quarterly financials और ownership history verify करते हुए।
- Document generated with **ReportLab**, no generative AI calls.
- हर शेयर की report PDF minimum 4 pages, >=15KB और required fundamental section text checks।
- **Telegram send से पहले** GitHub की durable `msk-daily-pdf-state` branch में उसी IST date की `CLAIMED` entry commit होगी। Workflow को फिर से चलाने पर यही claim duplicate रोकता है।
- Telegram `send_document` से वास्तविक positive message ID मिलने पर ही `SENT` का state update। Network fail या receipt missing = claim lock; automatic resend नहीं। Admin manual verification आवश्यक।
- `main.py` के existing buttons, webhook और Telegram handlers नहीं बदले गए।
- यह नया schedule **MSK Bot repo** में है और AiAutoUpdate/50-Day coding controller से अलग है।

## सीमाएँ

- GitHub Actions weekday schedule कई बार delayed/missed हो सकता है; exact समय और हर working day successful PDF की guarantee नहीं है।
- Exchange holiday calendar direct connection नहीं है: actual daily closing candle missing रहने पर safe skip।
- अलग free GitHub Actions minutes shared budget से खर्च होते हैं। Free quota खत्म होने पर scheduled work बंद हो सकता है।
- 30 स्टॉक्स की सूची कोई recommendation नहीं है; quality gate पास न होने पर कोई stock publish नहीं होगा।
- User-specific portfolio recommendation, personalized buy/sell और verified live CMP का दावा नहीं।

## Manual offline tests

```bash
python -m pip install -r requirements.txt pypdf
python -m unittest discover -s tests -p test_msk_daily_fundamental_pdf.py -v
```

Live Telegram पर test तभी किया जाए जब target एक स्पष्ट admin/private test chat हो और keys सुरक्षित configured हों।
