# LumaStore E-Commerce Management System

LumaStore is a responsive storefront and admin demo built with HTML, CSS, vanilla JavaScript, a Python HTTP API, and SQLite. Product inventory, reviews, and demo orders are stored in the database; the shopping cart stays in the browser.

## Run locally

1. Install Python 3.10 or later.
2. Set a private admin password and session-signing secret in the same PowerShell window:

   ```powershell
   $env:ADMIN_PASSWORD = "choose-a-strong-private-password"
   $env:ADMIN_SESSION_SECRET = [guid]::NewGuid().ToString() + [guid]::NewGuid().ToString()
   ```

3. From this folder, run the server:
   - On the original Windows setup: `& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" server.py`
   - If Python is on your PATH: `python server.py`
4. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and use **Admin sign in** to manage products and orders.

The server creates `data/lumastore.sqlite3` on first run and seeds the sample products and orders. Restarting the server preserves local data. Do not commit the password or session secret.

## Deploy to Render

The root `render.yaml` Blueprint defines a free Python web service and does not create any paid databases or other paid resources. In Render, create a Blueprint from this repository and provide a strong `ADMIN_PASSWORD` when prompted; Render generates the session-signing secret. The service uses SQLite and can sleep when idle.

**Free hosting limitation:** Render's free web service has an ephemeral filesystem. SQLite data, including admin changes, reviews, and demo orders, can be erased when the service restarts or redeploys. The app seeds its sample catalog again when that happens. Do not rely on free SQLite hosting for durable inventory or customer orders.

After the first successful deploy, Render assigns an HTTPS `onrender.com` address. That service serves both the storefront and API. GitHub Pages remains a static-only demo and cannot access the backend. A custom `name.com` address requires a domain that you own and configure separately.

## Included API and safeguards

- `GET /api/health` and `GET /api/state`
- Admin-authenticated product creation, edits, deletion, and order-status updates
- Public product reviews and demo-order creation
- HTTP-only, same-site admin session cookie; failed login attempts are rate limited
- Public state responses omit order details unless the administrator is signed in

Checkout is explicitly a demo: no payment, shipping address, card details, email address, or receipt is collected or processed. Do not use this app to accept real payments or customer orders without adding a payment provider, fulfillment flow, and operational safeguards.

## Demo scenarios

1. Add products to the bag and place a demo order. The backend creates an order and decrements stock.
2. Sign in to **Admin → Products**, create or edit an item, then refresh to confirm it persists.
3. In **Admin → Orders**, update an order status and refresh to confirm it persists.
4. Open a product and post a review. Its review and updated rating are stored in the database. On the Render free service, these changes may be lost after a restart or redeploy.
