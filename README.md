# LumaStore E-Commerce Management System

## Overview

LumaStore is a responsive, frontend-only e-commerce project built for the Advanced Frontend Development e-commerce brief. It uses HTML5, CSS3 and vanilla JavaScript (ES6+) with Local Storage for persistence. No backend is required.

## Features

- Customer storefront with responsive product catalogue, category filters, live search and sorting.
- Individual product details, ratings, customer review entry and quick add-to-cart actions.
- Cart drawer supporting quantity updates, item removal and price calculation.
- Validated simulated checkout that creates a new order and confirmation state.
- Demo customer sign-in interface with validation.
- Admin command center with sales summary cards, CSS data chart, top-selling products and recent-order feed.
- Admin product inventory tools: search, create, edit and delete products.
- Order table with filtering and status updates.
- Persisted products, cart and orders via `localStorage`.

## Run the project

Open `index.html` in a modern browser. An internet connection is only used to load the optional Google Fonts; all application functionality is contained in the project files.

## Test scenarios

1. Add one or more products through the quick-add button, open **Bag**, change quantities and proceed through checkout using any valid form details (card number needs 12-19 digits/spaces).
2. Open **Admin**, add or edit a product, then return to **Shop** to confirm the catalogue updates.
3. In **Admin → Orders**, change an order status and verify the overview reflects the update.
4. Refresh the browser: created products, cart contents and orders remain available.

## Technologies

Semantic HTML5, CSS Grid, Flexbox, responsive media queries, JavaScript ES6+, DOM manipulation, event delegation, form validation and Local Storage.
