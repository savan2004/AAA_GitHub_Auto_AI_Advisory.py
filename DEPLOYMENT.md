# 🚀 Render Deployment - FIXED VERSION

## What Was Fixed

### 1. Error 409 (Conflict) ✅
**Problem:** Multiple webhook/polling instances
**Fix:** 
- Added `skip_pending=True` to bot init
- Proper webhook cleanup with `delete_webhook(drop_pending_updates=True)`
- Auto-recovery on polling errors

### 2. AI Not Generating Results ✅
**Problem:** GROQ API errors or silent failures
**Fix:**
- AI initialization with test call
- Better error handling with fallbacks
- Improved JSON parsing with regex
- Clear error messages

### 3. Stock Data Errors ✅
**Problem:** yfinance API changes, empty data
**Fix:**
- Updated to yfinance 0.2.28 (stable)
- Minimum 20-day data requirement
- Better error messages
- Proper symbol normalization

### 4. Watchlist All Errors ✅
**Problem:** One stock error breaks entire watchlist
**Fix:**
- Individual try-except per stock
- Continues processing on errors
- Shows specific error per stock
- Never crashes entire list

## 📋 Environment Variables for Render

Copy-paste these EXACT values in Render Dashboard:

```bash
# Required
TELEGRAM_TOKEN=8461087780:AAG85fg8dWmVJyCW0E_5xgrS1Qc3abUgN2o

# Required for AI
GROQ_API_KEY=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

# Optional (for news)
NEWS_API_KEY=47fb3f33527944ed982e6e48cc856b23

# Required for Render
PORT=10000
PYTHON_VERSION=3.9.18
```

## 🔧 Render Configuration

### Build Settings

**Build Command:**
```bash
pip install -r requirements.txt
```

**Start Command:**
```bash
python main.py
```

**Environment:** Python 3

**Instance Type:** Free

**Region:** Singapore (best for India)

## ✅ Deployment Steps

### 1. Push to GitHub

```bash
git init
git add main.py requirements.txt README.md
git commit -m "AI Stock Bot - Fixed Version"
git branch -M main
git remote add origin YOUR_REPO_URL
git push -u origin main
```

### 2. Create Render Service

1. Go to https://render.com
2. New → Web Service
3. Connect GitHub repo
4. Configure settings (above)
5. Add environment variables
6. Click "Create Web Service"

### 3. Verify Deployment

**Check Logs:**
Look for:
```
✅ Telegram: OK
✅ GROQ: OK
✅ News: OK (or SKIP)
✅ Watchlist: 8 stocks
🔄 Polling...
✅ BOT ONLINE!
```

**Test Health Endpoint:**
Visit: `https://your-app.onrender.com`

Should show:
```
🤖 Bot Online
Time: 2026-02-13...
GROQ: ✅
Stocks: 8
```

**Test Bot:**
1. Open Telegram
2. Search your bot
3. Send `/start`
4. Try: `BEL` or `RELIANCE`

## 🐛 Common Issues & Fixes

### Issue: Error 409 Still Happening

**Solution:**
```bash
# In Render logs, you should see:
"Webhook: ..." (clearing message)
"🔄 Polling..." (starting fresh)

# If not:
1. Go to BotFather on Telegram
2. Send /mybots
3. Select your bot
4. Bot Settings → Delete Bot
5. Create new bot with /newbot
6. Update TELEGRAM_TOKEN in Render
```

### Issue: "No data for BEL"

**Solution:**
BEL is Bharat Electronics Limited
- Full symbol is `BEL.NS`
- Bot auto-adds `.NS`
- Wait 30-60 seconds for data fetch
- If still fails, try: `BHARATELECTRONICS` or check if delisted

### Issue: Watchlist Shows All Errors

**Solution:**
```python
# Each stock is processed independently
# If seeing errors:
1. Check if stocks are valid NSE symbols
2. Some may be suspended/delisted
3. Update watchlist in main.py
4. Redeploy to Render
```

### Issue: AI Not Responding

**Solution:**
```bash
# Check GROQ API key:
1. Visit https://console.groq.com
2. Verify key is active
3. Check usage limits
4. Create new key if needed
5. Update in Render environment vars
6. Restart service

# Bot will still work without AI using math models
```

### Issue: Slow Responses

**Causes & Solutions:**
- **yfinance delay**: Normal, 30-60s for data fetch
- **GROQ processing**: Usually 1-2s, spikes possible
- **Render free tier**: Limited resources
- **Multiple users**: Free tier has shared CPU

**Optimization:**
- Use GROQ (fastest AI)
- Avoid peak hours if slow
- Consider paid tier if needed

## 📊 Monitoring

### Check Logs
```bash
# In Render Dashboard:
Logs → See real-time output

# Look for:
✅ Successful analyses
⚠️ Warnings (non-critical)
❌ Errors (critical)
```

### Health Checks
```bash
# Render auto-pings health endpoint every 5 min
# Keeps bot awake on free tier
# Check: https://your-app.onrender.com
```

### Performance
```bash
# Metrics in Render:
- CPU usage
- Memory usage
- Request count
- Response times
```

## 🔄 Updates

### To Update Code:

```bash
# 1. Edit main.py locally
# 2. Test changes
# 3. Push to GitHub:
git add .
git commit -m "Update: description"
git push

# 4. Render auto-deploys (if enabled)
# Or manually: Render Dashboard → Manual Deploy
```

### To Update Dependencies:

```bash
# Edit requirements.txt
# Push to GitHub
# Render will rebuild automatically
```

## ✅ Success Checklist

- [ ] All environment variables set
- [ ] Build command correct
- [ ] Start command correct
- [ ] Logs show "BOT ONLINE!"
- [ ] Health endpoint returns 200
- [ ] Bot responds to `/start`
- [ ] Stock analysis works (try BEL)
- [ ] Watchlist shows data
- [ ] Market overview works
- [ ] No Error 409 in logs
- [ ] AI generating insights

## 🎉 You're Done!

Your bot is now:
- ✅ Running 24/7
- ✅ Error 409 fixed
- ✅ AI working properly
- ✅ Data fetching reliable
- ✅ Watchlist stable
- ✅ Auto-healing on errors

**Test with these symbols:**
- `RELIANCE` - Large cap
- `TCS` - IT sector
- `BEL` - Defense
- `DIXON` - Mid cap

---

## 🆘 Still Having Issues?

### Render Support
- Dashboard: https://dashboard.render.com
- Docs: https://render.com/docs
- Community: https://community.render.com

### GROQ Support
- Console: https://console.groq.com
- Docs: https://console.groq.com/docs

### Telegram Bot Support
- BotFather: @BotFather on Telegram
- Docs: https://core.telegram.org/bots

---

**Happy Investing! 📈**
