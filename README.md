# ⚽ Kickwise — Football Prediction App

## Files
- `index.html`     → The website (open on any phone browser)
- `app.py`         → Backend API server
- `requirements.txt` → Python dependencies
- `render.yaml`    → Render.com deployment config
- `A_mix2.xlsx`    → Your prediction model (add this yourself)

## How to Deploy (Step by Step)

### Step 1 — Upload to GitHub
1. Go to github.com on your phone/computer
2. Create a free account if you don't have one
3. Click "New repository" → name it `kickwise`
4. Upload ALL files in this folder including `A_mix2.xlsx`

### Step 2 — Deploy Backend to Render (free)
1. Go to render.com → create free account
2. Click "New" → "Web Service"
3. Connect your GitHub repo `kickwise`
4. Render auto-detects render.yaml and deploys
5. Wait ~5 minutes for first deploy
6. Copy your URL e.g. `https://kickwise-api.onrender.com`

### Step 3 — Update the frontend
1. Open `index.html`
2. Find the line: `const API_BASE = "https://kickwise-api.onrender.com";`
3. Replace with YOUR actual Render URL
4. Save

### Step 4 — Host the frontend (free)
1. Go to netlify.com → free account
2. Drag and drop just the `index.html` file
3. Netlify gives you a URL like `kickwise.netlify.app`
4. Open that URL on your phone — done!

## Daily Use
- Open your Netlify link on your phone
- Select league from dropdown
- Tap "Analyze Today"
- Results appear instantly!

## Notes
- Render free tier spins down after inactivity
  → First request takes ~30 seconds to wake up
  → After that, it's fast
- Data refreshes live from SoccerStats every time you tap
