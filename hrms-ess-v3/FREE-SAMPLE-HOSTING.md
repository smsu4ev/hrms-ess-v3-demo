# HRMS + ESS v2 — FREE Sample Testing Hosting Guide

For **sample/demo testing only** (fake data). For the real system with real
employee data, use the AWS plan in `DEPLOY-AWS.md`. Free tiers checked Oct 2026.

## Recommendation

**Use Render (free) for the sample.** Easiest for this Flask app, no card
needed, gives a public HTTPS link like `https://hrms-ess-demo.onrender.com`
that management can open and test.

## Option 1 — Render (recommended for sample) ⭐

Free tier facts:
- 512 MB RAM / 0.1 CPU, 750 instance hours/month (enough for ONE service)
- Sleeps after 15 minutes idle — first visit after sleep takes ~1 minute to wake
- Free Postgres (1 GB) is **deleted 30 days after creation**; the free
  filesystem is ephemeral, so demo data can reset on redeploy. Demo only.
- No payment card needed to start.

Steps:
1. Put this `hrms-ess-v2` folder into a GitHub repository (free account).
2. render.com → sign up (free) → **New → Blueprint** → connect the repo.
   Render reads `render.yaml` in this folder automatically.
3. Wait for the build/deploy to finish (few minutes).
4. Open the service's **Shell** in Render and run: `python seed_demo.py`
5. Open the URL and log in: **HRADMIN / Demo@123** (admin),
   **MGR001 / Demo@123** (manager), **EMP001 / Demo@123** (employee).

## Option 2 — PythonAnywhere (free, stays awake)

- Beginner account is genuinely free, no card; one web app at
  `your-username.pythonanywhere.com`; app does not cold-sleep like Render.
- Limits: 512 MB disk, no custom domain, free app must be renewed by
  clicking "Run until 3 months from today" every 3 months.
- Setup is manual (upload files, configure WSGI file) — a little slower
  to set up than Render, better if the demo must stay reachable for weeks.

## Option 3 — Oracle Cloud Always Free (free forever, strongest)

- Always Free ARM VM: **2 OCPU / 12 GB RAM**, 200 GB storage, free forever —
  easily runs this app + database permanently, same setup as `DEPLOY-AWS.md`.
- Trade-offs: signup needs a real card for identity verification (no charge),
  strict fraud screening, and free capacity can be unavailable in a region.
- Best choice if the "sample" may quietly become the long-term system.

## Option 4 — Your existing AWS account

- New AWS accounts (from July 2025) get a **Free Plan: $100 sign-up credit**
  (up to $100 more from onboarding activities), valid until the free plan
  ends (6 months or credits used, whichever first). Older accounts have the
  classic 12-month free tier (750 hrs/month of t2.micro/t3.micro + RDS micro).
- Costs to watch even on "free": a public IPv4 address is ~$3.60/month
  unless covered by the legacy free tier.
- Use this if you want the sample on the same cloud as production.

## Not free any more (skip for sample)
- **Heroku** — no free dynos. **Fly.io** — no permanent free tier (trial only).
- **Railway** — $5 trial credit for 30 days, then a $1/month free plan that
  is too small to keep this app running all month.

## Sample testing rules
- Use **only demo data** (`seed_demo.py`) on free hosting — never upload the
  real staff master or real salaries to a free tier.
- Demo passwords (Demo@123) are public in this guide — that is fine for a
  fake-data demo and unacceptable for production.
