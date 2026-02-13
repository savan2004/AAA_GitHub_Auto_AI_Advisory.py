# 🚀 Render Deployment Guide

Complete step-by-step guide to deploy your AI Stock Advisory Bot on Render for FREE.

## 📋 Prerequisites

Before starting, you need:
1. ✅ GitHub account
2. ✅ Render account (free)
3. ✅ Telegram bot token
4. ✅ At least one free AI API key (GROQ recommended)

## 🎯 Step-by-Step Deployment

### Step 1: Get Your API Keys (5 minutes)

#### 1.1 Telegram Bot Token
1. Open Telegram
2. Search for `@BotFather`
3. Send `/newbot`
4. Choose a name: `My Stock Advisory Bot`
5. Choose a username: `mystock_advisory_bot` (must end with 'bot')
6. Copy the token (looks like: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)
7. Save it somewhere safe

#### 1.2 GROQ API Key (Recommended - Fastest AI)
1. Go to: https://console.groq.com
2. Click "Sign In" (or create account)
3. Go to "API Keys" section
4. Click "Create API Key"
5. Name it: `StockBot`
6. Copy the key (starts with `gsk_...`)
7. Save it

#### 1.3 Google Gemini (Optional - Backup AI)
1. Go to: https://makersuite.google.com/app/apikey
2. Click "Get API Key"
3. Click "Create API key in new project"
4. Copy the key
5. Save it

### Step 2: Prepare GitHub Repository (5 minutes)

#### Option A: Use Existing Repository
1. Go to GitHub
2. Fork this repository
3. Your fork is ready!

#### Option B: Create New Repository
1. Go to GitHub → New Repository
2. Name: `ai-stock-advisory-bot`
3. Make it Public or Private
4. Create repository
5. Upload these files:
   - `Main.py`
   - `requirements.txt`
   - `README.md`
   - `.env.example`

### Step 3: Deploy on Render (10 minutes)

#### 3.1 Create Render Account
1. Go to: https://render.com
2. Click "Get Started"
3. Sign up with GitHub
4. Authorize Render to access your repositories

#### 3.2 Create New Web Service
1. In Render Dashboard, click "New +"
2. Select "Web Service"
3. Click "Build and deploy from a Git repository"
4. Click "Next"

#### 3.3 Connect Repository
1. Find your repository in the list
2. Click "Connect"
3. If not listed:
   - Click "Configure account"
   - Grant access to your repository
   - Refresh and find it

#### 3.4 Configure Service

Fill in the form:

**Basic Settings:**
```
Name: ai-stock-advisory-bot
Region: Choose closest to you (Singapore for India)
Branch: main (or master)
Root Directory: (leave blank)
```

**Build & Deploy:**
```
Runtime: Python 3
Build Command: pip install -r requirements.txt
Start Command: python Main.py
```

**Instance Type:**
```
Select: Free
```

Click "Advanced" to expand more options.

#### 3.5 Add Environment Variables

In the "Environment Variables" section, click "Add Environment Variable" for each:

**Required Variables:**

1. Variable 1:
   ```
   Key: TELEGRAM_TOKEN
   Value: [paste your telegram bot token]
   ```

2. Variable 2:
   ```
   Key: GROQ_API_KEY
   Value: [paste your GROQ API key]
   ```

**Optional Variables (add if you have them):**

3. Variable 3:
   ```
   Key: GEMINI_API_KEY
   Value: [paste your Gemini API key]
   ```

4. Variable 4:
   ```
   Key: HUGGINGFACE_TOKEN
   Value: [paste your HuggingFace token]
   ```

5. Variable 5:
   ```
   Key: PORT
   Value: 10000
   ```

#### 3.6 Deploy!
1. Click "Create Web Service"
2. Render will start building (5-10 minutes)
3. Watch the logs for progress

### Step 4: Verify Deployment (2 minutes)

#### 4.1 Check Render Logs
In Render dashboard:
1. Go to your service
2. Click "Logs" tab
3. Look for:
   ```
   ✅ GROQ AI Initialized
   ✅ Bot is ONLINE and ready!
   🌐 Health server running on port 10000
   ```

#### 4.2 Check Health Endpoint
1. Copy your service URL (looks like: `https://ai-stock-advisory-bot.onrender.com`)
2. Open it in browser
3. You should see: "🤖 AI Stock Advisory Bot ✅ Status: ONLINE"

#### 4.3 Test Telegram Bot
1. Open Telegram
2. Search for your bot username
3. Send `/start`
4. Bot should respond with welcome message!

## ✅ Deployment Complete!

Your bot is now live 24/7 on Render!

## 🔧 Post-Deployment

### Monitor Your Bot

**Check Logs:**
```
Render Dashboard → Your Service → Logs
```

**Check Metrics:**
```
Render Dashboard → Your Service → Metrics
```

### Update Bot Code

When you want to update:
1. Edit files on GitHub
2. Commit changes
3. Render auto-deploys (if "Auto-Deploy" is enabled)

Or manually:
```
Render Dashboard → Your Service → Manual Deploy → Deploy latest commit
```

### Restart Bot

If bot stops responding:
```
Render Dashboard → Your Service → Manual Deploy → Clear build cache & deploy
```

## 🐛 Troubleshooting

### Problem: Bot not responding

**Check 1: Service Running?**
- Go to Render dashboard
- Check if service status is "Live"
- If "Failed", check logs

**Check 2: Environment Variables Set?**
- Go to Environment tab
- Verify TELEGRAM_TOKEN is correct
- Verify at least one AI key is set

**Check 3: Bot Token Valid?**
- Message @BotFather on Telegram
- Send `/mybots`
- Check if bot exists

**Fix: Restart Service**
```
Render Dashboard → Manual Deploy → Deploy latest commit
```

### Problem: Bot slow to respond

**Cause:** Free tier has limited resources

**Solutions:**
1. Use GROQ (fastest AI)
2. Optimize code (already done in Main.py)
3. Upgrade to paid tier ($7/month for more resources)

### Problem: Service sleeping

**Cause:** Render free tier sleeps after 15 minutes of inactivity

**Fix:** Health check keeps it awake automatically!

The bot already includes:
```python
# Health server runs on PORT 10000
# Render pings it every 5 minutes
# Bot stays awake!
```

### Problem: Build Failed

**Common Causes:**
1. Missing files (`Main.py` or `requirements.txt`)
2. Wrong Python version
3. Syntax errors in code

**Fix:**
1. Check all files are in repository
2. Verify `requirements.txt` is correct
3. Test code locally first
4. Check Render logs for specific error

### Problem: Environment Variables Not Working

**Fix:**
1. Go to Environment tab
2. Delete and re-add variables
3. Make sure no extra spaces
4. Redeploy

## 📊 Free Tier Limits

**Render Free Tier:**
- ✅ 750 hours/month (enough for 24/7)
- ✅ 512 MB RAM
- ✅ Shared CPU
- ⚠️ Sleeps after 15 min inactivity (but health check prevents this)
- ⚠️ Public repositories only (or upgrade for private)

**AI API Free Tiers:**
- GROQ: 30 req/min, 14,400 tokens/min ✅
- Gemini: 60 req/min ✅
- HuggingFace: Rate limited ⚠️

These limits are MORE than enough for personal use!

## 🎓 Best Practices

### 1. Security
```
✅ Never commit .env file to GitHub
✅ Use Environment Variables in Render
✅ Regenerate keys if exposed
```

### 2. Monitoring
```
✅ Check logs daily (first week)
✅ Test bot functionality
✅ Monitor API usage
```

### 3. Maintenance
```
✅ Update dependencies monthly
✅ Review error logs weekly
✅ Test new features before deploy
```

### 4. Scaling
```
When bot gets popular:
1. Upgrade to Render paid tier ($7/mo)
2. Add more AI providers
3. Implement rate limiting
4. Add analytics
```

## 🔄 Update Process

### Update Bot Code:
1. Edit files on GitHub
2. Commit changes
3. Push to main branch
4. Render auto-deploys (if enabled)
5. Check logs for success

### Update Dependencies:
1. Edit `requirements.txt`
2. Commit and push
3. Render rebuilds automatically
4. Test bot functionality

### Add Features:
1. Develop locally
2. Test thoroughly
3. Push to GitHub
4. Render deploys
5. Verify in production

## 📞 Support

### Render Support:
- Dashboard: https://dashboard.render.com
- Docs: https://render.com/docs
- Community: https://community.render.com

### Bot Support:
- Check README.md for features
- Review Main.py for code
- Open GitHub issue for bugs

## 🎉 Success Checklist

Before marking deployment as complete:

- [ ] Service status shows "Live" in Render
- [ ] Health endpoint returns 200 OK
- [ ] Bot responds to `/start` command
- [ ] Stock analysis works (test with "RELIANCE")
- [ ] Market analysis loads
- [ ] Portfolio scanner runs
- [ ] No errors in logs
- [ ] Response time under 30 seconds
- [ ] Environment variables are set
- [ ] Auto-deploy is enabled

## 🚀 You're All Set!

Your AI Stock Advisory Bot is now:
- ✅ Running 24/7 on Render
- ✅ Using free AI providers
- ✅ Auto-healing from errors
- ✅ Providing deep stock analysis
- ✅ Helping investors make informed decisions

**Share your bot with friends and enjoy!**

---

**Need Help?**
- Check Render logs first
- Review troubleshooting section
- Test locally to isolate issues
- Open GitHub issue if stuck

**Happy Investing! 📈**
