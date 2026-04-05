"""
E-Commerce Agents — Database Seeder

Seeds the database with realistic e-commerce data:
- 20 users (1 admin, 2 power_users, 2 sellers, 15 customers)
- 50 products across 5 categories
- 200 orders with status distribution
- 500 reviews (5% fake)
- 3 warehouses, 3 carriers, shipping rates
- 15 coupons, 5 promotions, 3 loyalty tiers
- 90-day price history
- 6 agent catalog entries + permissions

Usage: uv run python -m scripts.seed
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import bcrypt

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"
)

# Deterministic seed for reproducible data
random.seed(42)


def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# ============================================================
# DATA DEFINITIONS
# ============================================================

USERS = [
    # (email, password, name, role, loyalty_tier, total_spend)
    ("admin.demo@gmail.com", "admin123", "Admin User", "admin", "gold", 5000),
    ("power.demo@gmail.com", "power123", "Power User", "power_user", "gold", 3500),
    ("power2.demo@gmail.com", "power123", "Sam Power", "power_user", "silver", 1200),
    ("seller.demo@gmail.com", "seller123", "Acme Store", "seller", "bronze", 0),
    ("seller2.demo@gmail.com", "seller123", "TechMart", "seller", "bronze", 0),
    ("alice.johnson@gmail.com", "customer123", "Alice Johnson", "customer", "gold", 4200),
    ("bob.smith@gmail.com", "customer123", "Bob Smith", "customer", "silver", 1800),
    ("carol.davis@gmail.com", "customer123", "Carol Davis", "customer", "silver", 1500),
    ("dave.wilson@gmail.com", "customer123", "Dave Wilson", "customer", "bronze", 450),
    ("emma.brown@gmail.com", "customer123", "Emma Brown", "customer", "bronze", 320),
    ("frank.miller@gmail.com", "customer123", "Frank Miller", "customer", "bronze", 200),
    ("grace.lee@gmail.com", "customer123", "Grace Lee", "customer", "bronze", 150),
    ("henry.taylor@gmail.com", "customer123", "Henry Taylor", "customer", "gold", 3800),
    ("iris.chen@gmail.com", "customer123", "Iris Chen", "customer", "silver", 1100),
    ("jack.anderson@gmail.com", "customer123", "Jack Anderson", "customer", "bronze", 600),
    ("kate.martinez@gmail.com", "customer123", "Kate Martinez", "customer", "bronze", 280),
    ("leo.garcia@gmail.com", "customer123", "Leo Garcia", "customer", "bronze", 90),
    ("mia.robinson@gmail.com", "customer123", "Mia Robinson", "customer", "bronze", 50),
    ("noah.thomas@gmail.com", "customer123", "Noah Thomas", "customer", "bronze", 75),
    ("olivia.white@gmail.com", "customer123", "Olivia White", "customer", "silver", 1350),
]

PRODUCTS = [
    # Electronics
    {"name": "Sony WH-1000XM5", "description": "Premium wireless noise-cancelling headphones with 30-hour battery life, multipoint connection, and speak-to-chat.", "category": "Electronics", "brand": "Sony", "price": 299.99, "original_price": 349.99, "rating": 4.7, "review_count": 0, "specs": {"type": "Over-ear", "battery": "30 hours", "noise_cancelling": True, "weight": "250g", "connectivity": "Bluetooth 5.2"}},
    {"name": "AirPods Max", "description": "Apple's premium over-ear headphones with computational audio, Active Noise Cancellation, and spatial audio.", "category": "Electronics", "brand": "Apple", "price": 449.99, "original_price": 549.00, "rating": 4.5, "review_count": 0, "specs": {"type": "Over-ear", "battery": "20 hours", "noise_cancelling": True, "weight": "384g", "chip": "H1"}},
    {"name": "Logitech MX Master 3S", "description": "Advanced wireless mouse with 8K DPI, quiet clicks, MagSpeed scroll, and multi-device support.", "category": "Electronics", "brand": "Logitech", "price": 99.99, "original_price": 129.99, "rating": 4.8, "review_count": 0, "specs": {"type": "Mouse", "dpi": 8000, "battery": "70 days", "connectivity": "Bluetooth + USB-C"}},
    {"name": "Samsung T7 Shield SSD 2TB", "description": "Portable SSD with IP65 water and dust resistance, 1,050MB/s read speed.", "category": "Electronics", "brand": "Samsung", "price": 89.99, "original_price": 109.99, "rating": 4.6, "review_count": 0, "specs": {"capacity": "2TB", "speed": "1050 MB/s", "interface": "USB 3.2", "water_resistant": True}},
    {"name": "Kindle Paperwhite Signature", "description": "6.8-inch e-reader with wireless charging, auto-adjusting front light, and 32GB storage.", "category": "Electronics", "brand": "Amazon", "price": 189.99, "original_price": 189.99, "rating": 4.7, "review_count": 0, "specs": {"display": "6.8 inch", "storage": "32GB", "waterproof": True, "battery": "10 weeks"}},
    {"name": "Apple Watch Series 10", "description": "Smartwatch with always-on Retina display, blood oxygen, ECG, and crash detection.", "category": "Electronics", "brand": "Apple", "price": 399.99, "original_price": 399.99, "rating": 4.6, "review_count": 0, "specs": {"display": "46mm", "water_resistance": "50m", "battery": "18 hours", "gps": True}},
    {"name": "Anker 737 Power Bank", "description": "24,000mAh portable charger with 140W output, smart digital display.", "category": "Electronics", "brand": "Anker", "price": 109.99, "original_price": 109.99, "rating": 4.5, "review_count": 0, "specs": {"capacity": "24000mAh", "output": "140W", "ports": 3, "weight": "630g"}},
    {"name": "Sony Alpha a6700", "description": "Mirrorless camera with 26MP APS-C sensor, AI-based autofocus, 4K 120fps video.", "category": "Electronics", "brand": "Sony", "price": 1399.99, "original_price": 1399.99, "rating": 4.8, "review_count": 0, "specs": {"sensor": "26MP APS-C", "video": "4K 120fps", "autofocus": "AI-based", "weight": "493g"}},
    {"name": "JBL Charge 5", "description": "Portable Bluetooth speaker with IP67 rating, 20-hour playtime, and built-in powerbank.", "category": "Electronics", "brand": "JBL", "price": 139.99, "original_price": 179.95, "rating": 4.7, "review_count": 0, "specs": {"battery": "20 hours", "waterproof": "IP67", "output": "30W", "weight": "960g"}},
    {"name": "Raspberry Pi 5 8GB", "description": "Single-board computer with 2.4GHz quad-core Arm Cortex-A76, PCIe 2.0, dual 4K display.", "category": "Electronics", "brand": "Raspberry Pi", "price": 79.99, "original_price": 79.99, "rating": 4.6, "review_count": 0, "specs": {"cpu": "Cortex-A76 2.4GHz", "ram": "8GB", "ports": "2x USB3, 2x USB2, 2x HDMI"}},

    # Clothing
    {"name": "North Face Thermoball Eco Jacket", "description": "Lightweight insulated jacket with recycled ThermoBall fill, packable design.", "category": "Clothing", "brand": "The North Face", "price": 179.99, "original_price": 230.00, "rating": 4.5, "review_count": 0, "specs": {"material": "Recycled polyester", "insulation": "ThermoBall Eco", "weight": "400g", "packable": True}},
    {"name": "Patagonia Better Sweater", "description": "Classic fleece jacket made from 100% recycled polyester, Fair Trade Certified.", "category": "Clothing", "brand": "Patagonia", "price": 139.00, "original_price": 139.00, "rating": 4.7, "review_count": 0, "specs": {"material": "100% recycled polyester", "weight": "539g", "fair_trade": True}},
    {"name": "Nike Air Max 270", "description": "Lifestyle sneaker with large Air unit in the heel for all-day comfort.", "category": "Clothing", "brand": "Nike", "price": 129.99, "original_price": 160.00, "rating": 4.4, "review_count": 0, "specs": {"type": "Lifestyle", "sole": "Air Max 270", "closure": "Lace-up"}},
    {"name": "Levi's 501 Original Jeans", "description": "The original straight-fit jean with button fly, 100% cotton denim.", "category": "Clothing", "brand": "Levi's", "price": 69.50, "original_price": 69.50, "rating": 4.3, "review_count": 0, "specs": {"fit": "Straight", "material": "100% cotton", "closure": "Button fly"}},
    {"name": "Allbirds Wool Runners", "description": "Sustainable sneakers made from ZQ Merino wool, carbon-neutral.", "category": "Clothing", "brand": "Allbirds", "price": 98.00, "original_price": 98.00, "rating": 4.4, "review_count": 0, "specs": {"material": "Merino wool", "sole": "SweetFoam", "carbon_neutral": True}},
    {"name": "Arc'teryx Atom LT Hoody", "description": "Versatile insulated hoody with Coreloft insulation and breathable side panels.", "category": "Clothing", "brand": "Arc'teryx", "price": 259.00, "original_price": 259.00, "rating": 4.8, "review_count": 0, "specs": {"insulation": "Coreloft", "weight": "345g", "breathable": True}},
    {"name": "Uniqlo Ultra Light Down Jacket", "description": "Ultra-lightweight packable down jacket with premium 90% duck down.", "category": "Clothing", "brand": "Uniqlo", "price": 59.90, "original_price": 79.90, "rating": 4.3, "review_count": 0, "specs": {"fill": "90% duck down", "weight": "210g", "packable": True}},
    {"name": "Adidas Ultraboost 24", "description": "Running shoe with BOOST midsole, Continental rubber outsole, Primeknit+ upper.", "category": "Clothing", "brand": "Adidas", "price": 189.99, "original_price": 189.99, "rating": 4.6, "review_count": 0, "specs": {"type": "Running", "midsole": "BOOST", "outsole": "Continental rubber"}},
    {"name": "Columbia Silver Ridge Cargo Pants", "description": "Quick-dry hiking pants with UPF 50 sun protection and cargo pockets.", "category": "Clothing", "brand": "Columbia", "price": 55.00, "original_price": 65.00, "rating": 4.2, "review_count": 0, "specs": {"material": "Nylon ripstop", "upf": 50, "quick_dry": True}},
    {"name": "Hoka Clifton 9", "description": "Cushioned running shoe with early-stage Meta-Rocker and breathable mesh.", "category": "Clothing", "brand": "Hoka", "price": 145.00, "original_price": 145.00, "rating": 4.6, "review_count": 0, "specs": {"type": "Running", "cushion": "EVA", "drop": "5mm", "weight": "248g"}},

    # Home
    {"name": "Dyson V15 Detect", "description": "Cordless vacuum with laser dust detection, piezo sensor, LCD screen showing particle count.", "category": "Home", "brand": "Dyson", "price": 649.99, "original_price": 749.99, "rating": 4.6, "review_count": 0, "specs": {"runtime": "60 min", "suction": "230 AW", "laser": True, "weight": "3.1kg"}},
    {"name": "Nespresso Vertuo Next", "description": "Centrifusion coffee machine brewing 5oz to 18oz cups with barcode recognition.", "category": "Home", "brand": "Nespresso", "price": 159.99, "original_price": 199.00, "rating": 4.3, "review_count": 0, "specs": {"brew_sizes": "5oz, 8oz, 14oz, 18oz", "system": "Centrifusion", "water_tank": "37oz"}},
    {"name": "Philips Hue Starter Kit", "description": "Smart lighting kit with 4 A19 color bulbs and Hue Bridge for 50+ lights.", "category": "Home", "brand": "Philips", "price": 129.99, "original_price": 199.99, "rating": 4.5, "review_count": 0, "specs": {"bulbs": 4, "type": "A19 Color", "hub_included": True, "max_lights": 50}},
    {"name": "iRobot Roomba j9+", "description": "Self-emptying robot vacuum with PrecisionVision navigation and 3-stage cleaning.", "category": "Home", "brand": "iRobot", "price": 599.99, "original_price": 799.99, "rating": 4.4, "review_count": 0, "specs": {"navigation": "PrecisionVision", "auto_empty": True, "runtime": "120 min"}},
    {"name": "Instant Pot Duo Plus 6Qt", "description": "9-in-1 electric pressure cooker: pressure cook, slow cook, steam, saute, and more.", "category": "Home", "brand": "Instant Pot", "price": 89.99, "original_price": 119.99, "rating": 4.7, "review_count": 0, "specs": {"capacity": "6Qt", "functions": 9, "power": "1000W"}},
    {"name": "Breville Barista Express", "description": "Semi-automatic espresso machine with built-in conical burr grinder.", "category": "Home", "brand": "Breville", "price": 599.95, "original_price": 699.95, "rating": 4.5, "review_count": 0, "specs": {"grinder": "Conical burr", "pressure": "15 bar", "water_tank": "67oz"}},
    {"name": "Vitamix E310 Explorian", "description": "Professional-grade blender with variable speed control and 48oz container.", "category": "Home", "brand": "Vitamix", "price": 349.95, "original_price": 349.95, "rating": 4.7, "review_count": 0, "specs": {"motor": "2HP", "container": "48oz", "speeds": "Variable + pulse"}},
    {"name": "Dyson Pure Cool TP07", "description": "Air purifier and fan with HEPA H13 filter, real-time air quality display.", "category": "Home", "brand": "Dyson", "price": 419.99, "original_price": 569.99, "rating": 4.3, "review_count": 0, "specs": {"filter": "HEPA H13", "coverage": "800 sq ft", "oscillation": "350 degrees"}},
    {"name": "KitchenAid Artisan Stand Mixer", "description": "Iconic tilt-head stand mixer with 5Qt bowl, 10 speeds, planetary mixing action.", "category": "Home", "brand": "KitchenAid", "price": 379.99, "original_price": 449.99, "rating": 4.8, "review_count": 0, "specs": {"capacity": "5Qt", "speeds": 10, "motor": "325W", "action": "Planetary"}},
    {"name": "Casper Original Mattress Queen", "description": "All-foam mattress with zoned support, breathable cover, 100-night trial.", "category": "Home", "brand": "Casper", "price": 995.00, "original_price": 1295.00, "rating": 4.4, "review_count": 0, "specs": {"size": "Queen", "type": "All-foam", "layers": 4, "trial": "100 nights"}},

    # Sports
    {"name": "Garmin Forerunner 265", "description": "GPS running watch with AMOLED display, training readiness, race predictor.", "category": "Sports", "brand": "Garmin", "price": 349.99, "original_price": 449.99, "rating": 4.7, "review_count": 0, "specs": {"display": "AMOLED 1.3in", "battery": "13 days", "gps": True, "heart_rate": True}},
    {"name": "Hydro Flask 32oz Wide Mouth", "description": "Double-wall vacuum insulated water bottle, keeps cold 24hr / hot 12hr.", "category": "Sports", "brand": "Hydro Flask", "price": 44.95, "original_price": 44.95, "rating": 4.8, "review_count": 0, "specs": {"capacity": "32oz", "insulation": "TempShield", "material": "18/8 stainless steel"}},
    {"name": "Manduka PRO Yoga Mat 6mm", "description": "Professional-grade yoga mat with closed-cell surface, lifetime guarantee.", "category": "Sports", "brand": "Manduka", "price": 120.00, "original_price": 136.00, "rating": 4.7, "review_count": 0, "specs": {"thickness": "6mm", "material": "PVC", "length": "71in", "weight": "7.5lbs"}},
    {"name": "Theragun Elite", "description": "Percussive therapy device with QuietForce technology, OLED screen, 5 attachments.", "category": "Sports", "brand": "Therabody", "price": 299.00, "original_price": 399.00, "rating": 4.5, "review_count": 0, "specs": {"speed_range": "1750-2400 rpm", "battery": "120 min", "attachments": 5, "force": "40 lbs"}},
    {"name": "Yeti Hopper Flip 12 Cooler", "description": "Portable soft cooler with DryHide shell, ColdCell insulation, holds 12 cans.", "category": "Sports", "brand": "Yeti", "price": 249.99, "original_price": 249.99, "rating": 4.6, "review_count": 0, "specs": {"capacity": "12 cans + ice", "insulation": "ColdCell", "waterproof": True}},
    {"name": "Osprey Atmos AG 65 Backpack", "description": "Anti-gravity suspension backpack with fit-on-the-fly hipbelt, 65L capacity.", "category": "Sports", "brand": "Osprey", "price": 310.00, "original_price": 310.00, "rating": 4.7, "review_count": 0, "specs": {"capacity": "65L", "suspension": "Anti-Gravity", "weight": "4.7lbs"}},
    {"name": "Fitbit Charge 6", "description": "Fitness tracker with built-in GPS, heart rate zones, 7-day battery, Google integration.", "category": "Sports", "brand": "Fitbit", "price": 159.95, "original_price": 159.95, "rating": 4.3, "review_count": 0, "specs": {"display": "AMOLED", "battery": "7 days", "gps": True, "water_resistance": "50m"}},
    {"name": "TRX All-in-One Suspension Trainer", "description": "Portable suspension training system for full-body workouts, door anchor included.", "category": "Sports", "brand": "TRX", "price": 129.95, "original_price": 169.95, "rating": 4.5, "review_count": 0, "specs": {"weight_limit": "350 lbs", "includes": "Door anchor, workout guide", "weight": "1.7lbs"}},
    {"name": "Black Diamond Spot 400 Headlamp", "description": "400-lumen rechargeable headlamp with red night vision mode, IPX8 waterproof.", "category": "Sports", "brand": "Black Diamond", "price": 49.95, "original_price": 49.95, "rating": 4.5, "review_count": 0, "specs": {"lumens": 400, "battery": "Rechargeable + AAA", "waterproof": "IPX8", "weight": "85g"}},
    {"name": "Nalgene Wide Mouth 32oz", "description": "BPA-free Tritan water bottle, legendary durability, made in USA.", "category": "Sports", "brand": "Nalgene", "price": 14.99, "original_price": 14.99, "rating": 4.7, "review_count": 0, "specs": {"capacity": "32oz", "material": "Tritan", "bpa_free": True, "dishwasher_safe": True}},

    # Books
    {"name": "Designing Data-Intensive Applications", "description": "The big ideas behind reliable, scalable, and maintainable systems by Martin Kleppmann.", "category": "Books", "brand": "O'Reilly", "price": 45.49, "original_price": 59.99, "rating": 4.9, "review_count": 0, "specs": {"author": "Martin Kleppmann", "pages": 616, "format": "Paperback", "year": 2017}},
    {"name": "Staff Engineer", "description": "Leadership beyond the management track by Will Larson. Paths for senior IC engineers.", "category": "Books", "brand": "Self-published", "price": 35.00, "original_price": 35.00, "rating": 4.6, "review_count": 0, "specs": {"author": "Will Larson", "pages": 387, "format": "Paperback", "year": 2021}},
    {"name": "System Design Interview Vol 2", "description": "Step-by-step framework for system design interviews with 13 real-world systems.", "category": "Books", "brand": "ByteByteGo", "price": 39.99, "original_price": 39.99, "rating": 4.7, "review_count": 0, "specs": {"author": "Alex Xu", "pages": 434, "format": "Paperback", "year": 2022}},
    {"name": "The Pragmatic Programmer (20th Ed)", "description": "Classic guide to software craftsmanship, updated for modern development.", "category": "Books", "brand": "Addison-Wesley", "price": 49.99, "original_price": 59.99, "rating": 4.8, "review_count": 0, "specs": {"author": "Thomas & Hunt", "pages": 352, "format": "Hardcover", "year": 2019}},
    {"name": "Building Microservices (2nd Ed)", "description": "Designing fine-grained systems by Sam Newman. Patterns for distributed architectures.", "category": "Books", "brand": "O'Reilly", "price": 42.49, "original_price": 54.99, "rating": 4.5, "review_count": 0, "specs": {"author": "Sam Newman", "pages": 616, "format": "Paperback", "year": 2021}},
    {"name": "Clean Code", "description": "A handbook of agile software craftsmanship by Robert C. Martin.", "category": "Books", "brand": "Prentice Hall", "price": 37.49, "original_price": 49.99, "rating": 4.4, "review_count": 0, "specs": {"author": "Robert C. Martin", "pages": 464, "format": "Paperback", "year": 2008}},
    {"name": "Atomic Habits", "description": "Tiny changes, remarkable results. Practical guide to building good habits.", "category": "Books", "brand": "Avery", "price": 18.99, "original_price": 27.00, "rating": 4.8, "review_count": 0, "specs": {"author": "James Clear", "pages": 320, "format": "Hardcover", "year": 2018}},
    {"name": "Deep Work", "description": "Rules for focused success in a distracted world by Cal Newport.", "category": "Books", "brand": "Grand Central", "price": 16.99, "original_price": 28.00, "rating": 4.5, "review_count": 0, "specs": {"author": "Cal Newport", "pages": 296, "format": "Paperback", "year": 2016}},
    {"name": "The Manager's Path", "description": "A guide for tech leaders navigating growth and change by Camille Fournier.", "category": "Books", "brand": "O'Reilly", "price": 33.49, "original_price": 39.99, "rating": 4.6, "review_count": 0, "specs": {"author": "Camille Fournier", "pages": 244, "format": "Paperback", "year": 2017}},
    {"name": "Fundamentals of Software Architecture", "description": "An engineering approach to software architecture by Richards & Ford.", "category": "Books", "brand": "O'Reilly", "price": 47.49, "original_price": 59.99, "rating": 4.5, "review_count": 0, "specs": {"author": "Richards & Ford", "pages": 422, "format": "Paperback", "year": 2020}},
]

WAREHOUSES = [
    ("East Coast Warehouse", "Richmond, VA", "east"),
    ("Central Warehouse", "Dallas, TX", "central"),
    ("West Coast Warehouse", "Portland, OR", "west"),
]

CARRIERS = [
    ("Standard Shipping", "standard", 5.99),
    ("Express Shipping", "express", 14.99),
    ("Overnight Shipping", "overnight", 29.99),
]

COUPONS = [
    # (code, description, type, value, min_spend, max_discount, usage_limit, valid_until, categories, user_email)
    ("WELCOME10", "10% off your first order", "percentage", 10, 0, 50, None, None, None, None),
    ("TECHSAVE", "15% off Electronics", "percentage", 15, 100, 75, 200, None, ["Electronics"], None),
    ("TEAMGIFT", "Free gift wrapping for bulk orders", "fixed", 0, 0, None, None, None, None, None),
    ("LOYALTY20", "20% off for Gold members", "percentage", 20, 0, 100, None, None, None, None),
    ("SPRING25", "$25 off orders $150+", "fixed", 25, 150, None, 500, "2026-04-30", None, None),
    ("BOOKWORM", "10% off Books", "percentage", 10, 0, 20, 100, None, ["Books"], None),
    ("ALICE15", "Personal 15% off for Alice", "percentage", 15, 50, 40, 1, None, None, "alice.johnson@gmail.com"),
    ("FLASH20", "$20 off flash sale", "fixed", 20, 75, None, 300, "2026-04-10", None, None),
    ("SPORTS10", "10% off Sports gear", "percentage", 10, 0, 30, 150, None, ["Sports"], None),
    ("HOME15", "15% off Home items", "percentage", 15, 100, 60, 100, None, ["Home"], None),
    ("NEWUSER", "$10 off first order", "fixed", 10, 25, None, None, None, None, None),
    ("SUMMER30", "30% off Clothing", "percentage", 30, 50, 80, None, "2026-07-31", ["Clothing"], None),
    ("VIP50", "$50 off for VIP", "fixed", 50, 200, None, 10, None, None, "power.demo@gmail.com"),
    ("EXPIRED10", "Expired coupon", "percentage", 10, 0, 20, 100, "2026-01-01", None, None),
    ("BUNDLE5", "$5 off any order", "fixed", 5, 0, None, 1000, None, None, None),
]

PROMOTIONS = [
    {"name": "Tech Bundle Deal", "type": "bundle", "rules": {"products": ["Sony WH-1000XM5", "Samsung T7 Shield SSD 2TB"], "discount_pct": 10}, "days_active": 30},
    {"name": "Buy 2 Books Get 10% Off", "type": "buy_x_get_y", "rules": {"category": "Books", "min_quantity": 2, "discount_pct": 10}, "days_active": 60},
    {"name": "Spring Flash Sale", "type": "flash_sale", "rules": {"categories": ["Clothing", "Sports"], "discount_pct": 15}, "days_active": 7},
    {"name": "Home Essentials Pack", "type": "bundle", "rules": {"products": ["Instant Pot Duo Plus 6Qt", "Vitamix E310 Explorian"], "discount_pct": 12}, "days_active": 45},
    {"name": "Fitness Starter Kit", "type": "bundle", "rules": {"products": ["Fitbit Charge 6", "Hydro Flask 32oz Wide Mouth", "Manduka PRO Yoga Mat 6mm"], "discount_pct": 15}, "days_active": 30},
]

LOYALTY_TIERS = [
    ("bronze", 0, 0, None, False),
    ("silver", 1000, 5, 75.00, False),
    ("gold", 3000, 10, 0.00, True),
]

AGENT_CATALOG = [
    ("product-discovery", "Product Discovery", "Natural language product search with personalized recommendations, semantic search, and price tracking.", "Search & Discovery", "search", ["product_search", "semantic_search", "price_comparison", "trending"], False),
    ("order-management", "Order Management", "Order tracking, cancellation, modification, returns, and refund processing.", "Order Operations", "package", ["order_tracking", "cancellation", "returns", "refunds"], False),
    ("pricing-promotions", "Pricing & Promotions", "Coupon validation, cart optimization, loyalty discounts, and deal discovery.", "Pricing", "tag", ["coupon_validation", "cart_optimization", "loyalty_discounts"], True),
    ("review-sentiment", "Review & Sentiment", "Review analysis, sentiment breakdown, fake review detection, and seller response drafting.", "Analytics", "bar-chart", ["sentiment_analysis", "fake_detection", "review_search"], True),
    ("inventory-fulfillment", "Inventory & Fulfillment", "Stock checking, shipping estimation, carrier comparison, and fulfillment planning.", "Logistics", "truck", ["stock_check", "shipping_estimate", "carrier_comparison"], False),
    ("customer-support", "Customer Support", "Orchestrator agent that routes requests to specialist agents for comprehensive assistance.", "Support", "headphones", ["multi_agent_routing", "intent_classification", "conversation_management"], False),
]

REVIEW_TEMPLATES_POSITIVE = [
    "Excellent product! {reason}. Highly recommend to anyone looking for {use_case}.",
    "Love this {product_type}. {reason}. Worth every penny.",
    "Great quality and fast shipping. {reason}. Would buy again.",
    "Exactly what I needed. {reason}. {product_type} works perfectly.",
    "Five stars! {reason}. Best {product_type} I've owned.",
    "Really impressed with the build quality. {reason}.",
    "Been using this for a few weeks now and {reason}. Very happy with the purchase.",
    "Perfect gift for {use_case}. {reason}. Recipient loved it.",
]

REVIEW_TEMPLATES_NEUTRAL = [
    "Decent {product_type}. {reason}. Could be better in some areas but overall okay.",
    "It does what it says. {reason}. Nothing special but gets the job done.",
    "Good for the price. {reason}. There are better options if budget isn't a concern.",
    "Average experience. {reason}. Some pros and cons to consider.",
]

REVIEW_TEMPLATES_NEGATIVE = [
    "Disappointed with this purchase. {reason}. Expected better quality.",
    "Not worth the price. {reason}. Looking for alternatives.",
    "Had issues right out of the box. {reason}. Considering returning.",
    "Below expectations. {reason}. {product_type} didn't hold up well.",
]

REVIEW_TEMPLATES_FAKE = [
    "Amazing product!!!!! Best thing ever!!!! Buy it now!!!! You won't regret it!!!!!",
    "This is the greatest {product_type} in the world. Perfect in every way. Nothing compares.",
    "I bought 10 of these for everyone I know. Life changing product. 100% perfect.",
    "BEST PURCHASE EVER. This {product_type} changed my life completely. Must buy!!!",
]

ADDRESSES = [
    {"street": "123 Main St", "city": "New York", "state": "NY", "zip": "10001", "country": "US"},
    {"street": "456 Oak Ave", "city": "Los Angeles", "state": "CA", "zip": "90001", "country": "US"},
    {"street": "789 Pine Rd", "city": "Chicago", "state": "IL", "zip": "60601", "country": "US"},
    {"street": "321 Elm Dr", "city": "Houston", "state": "TX", "zip": "77001", "country": "US"},
    {"street": "654 Maple Ln", "city": "Phoenix", "state": "AZ", "zip": "85001", "country": "US"},
    {"street": "987 Cedar Ct", "city": "Seattle", "state": "WA", "zip": "98101", "country": "US"},
    {"street": "147 Birch Way", "city": "Denver", "state": "CO", "zip": "80201", "country": "US"},
    {"street": "258 Walnut Blvd", "city": "Atlanta", "state": "GA", "zip": "30301", "country": "US"},
    {"street": "369 Spruce St", "city": "Boston", "state": "MA", "zip": "02101", "country": "US"},
    {"street": "741 Ash Ave", "city": "Miami", "state": "FL", "zip": "33101", "country": "US"},
]

ORDER_STATUSES_WEIGHTED = [
    ("placed", 10), ("confirmed", 15), ("shipped", 15),
    ("delivered", 40), ("returned", 10), ("cancelled", 10),
]


# ============================================================
# SEEDING FUNCTIONS
# ============================================================

async def seed_users(conn: asyncpg.Connection) -> dict[str, uuid.UUID]:
    """Insert users and return email -> id mapping."""
    user_ids = {}
    for email, password, name, role, tier, spend in USERS:
        pw_hash = hash_pw(password)
        row = await conn.fetchrow(
            """INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
               RETURNING id""",
            email, pw_hash, name, role, tier, Decimal(str(spend)),
        )
        user_ids[email] = row["id"]
    logger.info("Seeded %d users", len(user_ids))
    return user_ids


async def seed_products(conn: asyncpg.Connection, user_ids: dict[str, uuid.UUID]) -> list[dict]:
    """Insert products and return list with ids. Assigns seller ownership."""
    seller1_id = user_ids["seller.demo@gmail.com"]
    seller2_id = user_ids["seller2.demo@gmail.com"]

    products_with_ids = []
    for i, p in enumerate(PRODUCTS):
        # First 25 products -> seller.demo@gmail.com, remaining 25 -> seller2.demo@gmail.com
        seller_id = seller1_id if i < 25 else seller2_id
        row = await conn.fetchrow(
            """INSERT INTO products (name, description, category, brand, price, original_price, rating, review_count, specs, seller_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
               ON CONFLICT DO NOTHING
               RETURNING id""",
            p["name"], p["description"], p["category"], p["brand"],
            Decimal(str(p["price"])), Decimal(str(p.get("original_price", p["price"]))),
            Decimal(str(p["rating"])), p["review_count"],
            json.dumps(p["specs"]), seller_id,
        )
        if row:
            products_with_ids.append({**p, "id": row["id"]})
    logger.info("Seeded %d products", len(products_with_ids))
    return products_with_ids


async def seed_warehouses(conn: asyncpg.Connection) -> list[uuid.UUID]:
    """Insert warehouses and return ids."""
    ids = []
    for name, location, region in WAREHOUSES:
        row = await conn.fetchrow(
            "INSERT INTO warehouses (name, location, region) VALUES ($1, $2, $3) RETURNING id",
            name, location, region,
        )
        ids.append(row["id"])
    logger.info("Seeded %d warehouses", len(ids))
    return ids


async def seed_warehouse_inventory(conn: asyncpg.Connection, warehouse_ids: list[uuid.UUID], products: list[dict]) -> None:
    """Seed inventory with varied stock levels. Some products deliberately low/out of stock."""
    count = 0
    for i, product in enumerate(products):
        for j, wh_id in enumerate(warehouse_ids):
            # Dyson V15 = out of stock everywhere
            if product["name"] == "Dyson V15 Detect":
                qty = 0
            # Sony WH-1000XM5 = low stock at West
            elif product["name"] == "Sony WH-1000XM5" and j == 2:
                qty = 2
            else:
                qty = random.randint(5, 200)

            await conn.execute(
                "INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity) VALUES ($1, $2, $3)",
                wh_id, product["id"], qty,
            )
            count += 1
    logger.info("Seeded %d warehouse inventory records", count)


async def seed_carriers(conn: asyncpg.Connection) -> list[uuid.UUID]:
    """Insert carriers and return ids."""
    ids = []
    for name, speed, rate in CARRIERS:
        row = await conn.fetchrow(
            "INSERT INTO carriers (name, speed_tier, base_rate) VALUES ($1, $2, $3) RETURNING id",
            name, speed, Decimal(str(rate)),
        )
        ids.append(row["id"])
    logger.info("Seeded %d carriers", len(ids))
    return ids


async def seed_shipping_rates(conn: asyncpg.Connection, carrier_ids: list[uuid.UUID]) -> None:
    """Seed shipping rate matrix: 3 carriers x 3 regions x 3 regions."""
    regions = ["east", "central", "west"]
    # (standard_days, express_days, overnight_days) base — add distance modifier
    count = 0
    for ci, carrier_id in enumerate(carrier_ids):
        for rf in regions:
            for rt in regions:
                distance = 0 if rf == rt else (1 if abs(regions.index(rf) - regions.index(rt)) == 1 else 2)
                if ci == 0:  # standard
                    days_min, days_max = 5 + distance, 7 + distance
                    price = Decimal("5.99") + Decimal(str(distance * 2))
                elif ci == 1:  # express
                    days_min, days_max = 2 + distance, 3 + distance
                    price = Decimal("14.99") + Decimal(str(distance * 3))
                else:  # overnight
                    days_min, days_max = 1, 1 + distance
                    price = Decimal("29.99") + Decimal(str(distance * 5))

                await conn.execute(
                    """INSERT INTO shipping_rates (carrier_id, region_from, region_to, price, estimated_days_min, estimated_days_max)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    carrier_id, rf, rt, price, days_min, days_max,
                )
                count += 1
    logger.info("Seeded %d shipping rates", count)


async def seed_coupons(conn: asyncpg.Connection) -> None:
    """Seed coupons."""
    now = datetime.now(timezone.utc)
    for code, desc, dtype, value, min_spend, max_disc, limit, valid_until, cats, user_email in COUPONS:
        until = datetime.fromisoformat(valid_until).replace(tzinfo=timezone.utc) if valid_until else now + timedelta(days=365)
        await conn.execute(
            """INSERT INTO coupons (code, description, discount_type, discount_value, min_spend, max_discount,
               usage_limit, valid_from, valid_until, applicable_categories, user_specific_email)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)""",
            code, desc, dtype, Decimal(str(value)), Decimal(str(min_spend)),
            Decimal(str(max_disc)) if max_disc else None,
            limit, now - timedelta(days=30), until, cats, user_email,
        )
    logger.info("Seeded %d coupons", len(COUPONS))


async def seed_promotions(conn: asyncpg.Connection) -> None:
    """Seed promotions."""
    now = datetime.now(timezone.utc)
    for promo in PROMOTIONS:
        await conn.execute(
            """INSERT INTO promotions (name, type, rules, start_date, end_date)
               VALUES ($1, $2, $3::jsonb, $4, $5)""",
            promo["name"], promo["type"], json.dumps(promo["rules"]),
            now - timedelta(days=5), now + timedelta(days=promo["days_active"]),
        )
    logger.info("Seeded %d promotions", len(PROMOTIONS))


async def seed_loyalty_tiers(conn: asyncpg.Connection) -> None:
    """Seed loyalty tiers."""
    for name, min_spend, discount, free_ship, priority in LOYALTY_TIERS:
        await conn.execute(
            """INSERT INTO loyalty_tiers (name, min_spend, discount_pct, free_shipping_threshold, priority_support)
               VALUES ($1, $2, $3, $4, $5)""",
            name, Decimal(str(min_spend)), Decimal(str(discount)),
            Decimal(str(free_ship)) if free_ship else None, priority,
        )
    logger.info("Seeded %d loyalty tiers", len(LOYALTY_TIERS))


async def seed_orders(conn: asyncpg.Connection, user_ids: dict[str, uuid.UUID], products: list[dict]) -> list[uuid.UUID]:
    """Seed 200 orders with realistic distribution."""
    customer_emails = [e for e, _, _, r, _, _ in USERS if r == "customer"]
    statuses = []
    for status, weight in ORDER_STATUSES_WEIGHTED:
        statuses.extend([status] * weight)

    now = datetime.now(timezone.utc)
    order_ids = []

    for i in range(200):
        email = random.choice(customer_emails)
        user_id = user_ids[email]
        status = random.choice(statuses)
        address = random.choice(ADDRESSES)
        created = now - timedelta(days=random.randint(1, 90), hours=random.randint(0, 23))

        # 1-4 items per order
        num_items = random.randint(1, 4)
        order_products = random.sample(products, min(num_items, len(products)))
        total = Decimal("0")
        items = []
        for op in order_products:
            qty = random.randint(1, 3)
            price = Decimal(str(op["price"]))
            subtotal = price * qty
            total += subtotal
            items.append((op["id"], qty, price, subtotal))

        carrier = random.choice(CARRIERS)[0]
        tracking = f"TRK{random.randint(100000000, 999999999)}" if status in ("shipped", "out_for_delivery", "delivered") else None

        row = await conn.fetchrow(
            """INSERT INTO orders (user_id, status, total, shipping_address, shipping_carrier, tracking_number, created_at)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7) RETURNING id""",
            user_id, status, total, json.dumps(address), carrier, tracking, created,
        )
        order_id = row["id"]
        order_ids.append(order_id)

        # Insert order items
        for product_id, qty, price, subtotal in items:
            await conn.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal) VALUES ($1, $2, $3, $4, $5)",
                order_id, product_id, qty, price, subtotal,
            )

        # Insert status history
        status_flow = _get_status_flow(status)
        for si, s in enumerate(status_flow):
            ts = created + timedelta(hours=si * random.randint(6, 48))
            location = random.choice(["Warehouse", "Distribution Center", "Local Hub", "Out for delivery", ""]) if s in ("shipped", "out_for_delivery") else ""
            await conn.execute(
                "INSERT INTO order_status_history (order_id, status, notes, location, timestamp) VALUES ($1, $2, $3, $4, $5)",
                order_id, s, f"Order {s}", location, ts,
            )

    logger.info("Seeded %d orders", len(order_ids))
    return order_ids


def _get_status_flow(final_status: str) -> list[str]:
    """Return the status history leading to the final status."""
    full_flow = ["placed", "confirmed", "shipped", "out_for_delivery", "delivered"]
    if final_status == "cancelled":
        cut = random.randint(1, 2)
        return full_flow[:cut] + ["cancelled"]
    if final_status == "returned":
        return full_flow + ["returned"]
    try:
        idx = full_flow.index(final_status)
        return full_flow[:idx + 1]
    except ValueError:
        return [final_status]


async def seed_reviews(conn: asyncpg.Connection, user_ids: dict[str, uuid.UUID], products: list[dict]) -> None:
    """Seed 500 reviews with 5% fake patterns."""
    customer_emails = [e for e, _, _, r, _, _ in USERS if r == "customer"]
    reasons = ["great battery life", "excellent sound quality", "comfortable fit", "fast performance",
               "durable build", "easy setup", "good value", "beautiful design", "lightweight",
               "reliable connectivity"]
    use_cases = ["everyday use", "travel", "work from home", "outdoor activities", "running",
                 "gifts", "home office", "kitchen", "camping", "studying"]
    product_types = {
        "Electronics": "device", "Clothing": "item", "Home": "appliance",
        "Sports": "gear", "Books": "book",
    }

    count = 0
    for _ in range(500):
        product = random.choice(products)
        email = random.choice(customer_emails)
        user_id = user_ids[email]
        is_fake = random.random() < 0.05  # 5% fake
        pt = product_types.get(product["category"], "product")

        if is_fake:
            rating = 5
            template = random.choice(REVIEW_TEMPLATES_FAKE)
            body = template.format(product_type=pt)
            title = random.choice(["AMAZING!!!", "PERFECT!!!", "BEST EVER!!!", "MUST BUY!!!"])
            verified = False
        else:
            rating = random.choices([1, 2, 3, 4, 5], weights=[5, 10, 15, 35, 35])[0]
            reason = random.choice(reasons)
            use_case = random.choice(use_cases)
            if rating >= 4:
                template = random.choice(REVIEW_TEMPLATES_POSITIVE)
            elif rating == 3:
                template = random.choice(REVIEW_TEMPLATES_NEUTRAL)
            else:
                template = random.choice(REVIEW_TEMPLATES_NEGATIVE)
            body = template.format(reason=reason, use_case=use_case, product_type=pt)
            title = f"{'Great' if rating >= 4 else 'Okay' if rating == 3 else 'Disappointing'} {pt}"
            verified = random.random() < 0.7

        created = datetime.now(timezone.utc) - timedelta(days=random.randint(1, 180))
        await conn.execute(
            """INSERT INTO reviews (product_id, user_id, rating, title, body, verified_purchase, is_flagged, created_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            product["id"], user_id, rating, title, body, verified, is_fake, created,
        )
        count += 1

    # Update review counts on products
    await conn.execute(
        """UPDATE products SET review_count = sub.cnt
           FROM (SELECT product_id, COUNT(*) as cnt FROM reviews GROUP BY product_id) sub
           WHERE products.id = sub.product_id"""
    )
    logger.info("Seeded %d reviews", count)


async def seed_price_history(conn: asyncpg.Connection, products: list[dict]) -> None:
    """Seed 90 days of price history for all products."""
    now = datetime.now(timezone.utc)
    count = 0
    for product in products:
        base_price = float(product["price"])
        for day in range(90):
            recorded = now - timedelta(days=90 - day)
            # Occasionally have a sale (10% chance)
            if random.random() < 0.10:
                price = base_price * random.uniform(0.80, 0.95)
            else:
                price = base_price * random.uniform(0.97, 1.03)
            price = round(price, 2)
            await conn.execute(
                "INSERT INTO price_history (product_id, price, recorded_at) VALUES ($1, $2, $3)",
                product["id"], Decimal(str(price)), recorded,
            )
            count += 1
    logger.info("Seeded %d price history records", count)


async def seed_restock_schedule(conn: asyncpg.Connection, warehouse_ids: list[uuid.UUID], products: list[dict]) -> None:
    """Seed restock schedules for low-stock items."""
    now = datetime.now(timezone.utc)
    restocks = [
        ("Dyson V15 Detect", 0, 50, 15),    # Out of stock → restock in 15 days
        ("Dyson V15 Detect", 1, 30, 15),
        ("Dyson V15 Detect", 2, 40, 15),
        ("Sony WH-1000XM5", 2, 100, 7),     # Low stock at West → restock in 7 days
        ("Casper Original Mattress Queen", 0, 10, 20),
        ("Sony Alpha a6700", 1, 15, 10),
        ("iRobot Roomba j9+", 2, 25, 12),
        ("Breville Barista Express", 0, 20, 8),
        ("Arc'teryx Atom LT Hoody", 1, 30, 14),
        ("Garmin Forerunner 265", 2, 40, 5),
    ]
    product_map = {p["name"]: p["id"] for p in products}
    count = 0
    for pname, wh_idx, qty, days_out in restocks:
        if pname in product_map:
            await conn.execute(
                "INSERT INTO restock_schedule (product_id, warehouse_id, expected_quantity, expected_date) VALUES ($1, $2, $3, $4)",
                product_map[pname], warehouse_ids[wh_idx], qty, (now + timedelta(days=days_out)).date(),
            )
            count += 1
    logger.info("Seeded %d restock schedules", count)


async def seed_agent_catalog(conn: asyncpg.Connection) -> None:
    """Seed agent catalog entries."""
    for name, display, desc, category, icon, caps, requires_approval in AGENT_CATALOG:
        await conn.execute(
            """INSERT INTO agent_catalog (name, display_name, description, category, icon, capabilities, requires_approval)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (name) DO UPDATE SET display_name = EXCLUDED.display_name""",
            name, display, desc, category, icon, caps, requires_approval,
        )
    logger.info("Seeded %d agent catalog entries", len(AGENT_CATALOG))


async def seed_agent_permissions(conn: asyncpg.Connection, user_ids: dict[str, uuid.UUID]) -> None:
    """Grant agent permissions to admin and power users."""
    admin_id = user_ids["admin.demo@gmail.com"]
    agents = [a[0] for a in AGENT_CATALOG]

    # Admin gets access to all agents
    for agent_name in agents:
        await conn.execute(
            """INSERT INTO agent_permissions (user_id, agent_name, role, granted_by)
               VALUES ($1, $2, 'admin', $1) ON CONFLICT DO NOTHING""",
            admin_id, agent_name,
        )

    # Power users get access to all agents
    for pu_email in ["power.demo@gmail.com", "power2.demo@gmail.com"]:
        pu_id = user_ids[pu_email]
        for agent_name in agents:
            await conn.execute(
                """INSERT INTO agent_permissions (user_id, agent_name, role, granted_by)
                   VALUES ($1, $2, 'user', $3) ON CONFLICT DO NOTHING""",
                pu_id, agent_name, admin_id,
            )

    # Regular customers get access to non-approval-required agents
    free_agents = [a[0] for a in AGENT_CATALOG if not a[6]]
    customer_emails = [e for e, _, _, r, _, _ in USERS if r == "customer"]
    for email in customer_emails:
        uid = user_ids[email]
        for agent_name in free_agents:
            await conn.execute(
                """INSERT INTO agent_permissions (user_id, agent_name, role, granted_by)
                   VALUES ($1, $2, 'user', $3) ON CONFLICT DO NOTHING""",
                uid, agent_name, admin_id,
            )

    logger.info("Seeded agent permissions for admin, power users, and customers")


# ============================================================
# MAIN
# ============================================================

async def connect_with_retry(
    dsn: str, *, max_retries: int = 15, delay: float = 2.0
) -> asyncpg.Connection:
    """Connect to PostgreSQL with retries for first-run init race conditions."""
    for attempt in range(1, max_retries + 1):
        try:
            return await asyncpg.connect(dsn)
        except (asyncpg.InvalidPasswordError, OSError, asyncpg.CannotConnectNowError) as exc:
            if attempt == max_retries:
                raise
            logger.warning(
                "Connection attempt %d/%d failed (%s), retrying in %.0fs...",
                attempt, max_retries, exc, delay,
            )
            await asyncio.sleep(delay)
    raise RuntimeError("Unreachable")


async def main() -> None:
    logger.info("Connecting to database: %s", DATABASE_URL.split("@")[-1])
    conn = await connect_with_retry(DATABASE_URL)

    try:
        # Check if already seeded
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
        if count > 0:
            logger.info("Database already has %d users — clearing and re-seeding", count)
            # Truncate all tables in dependency order
            await conn.execute("""
                TRUNCATE agent_execution_steps, usage_logs, messages, conversations,
                         agent_permissions, access_requests, agent_catalog,
                         restock_schedule, shipping_rates, warehouse_inventory,
                         price_history, product_embeddings, reviews,
                         order_status_history, order_items, returns, orders,
                         promotions, coupons, loyalty_tiers,
                         carriers, warehouses, products, users
                CASCADE
            """)

        user_ids = await seed_users(conn)
        products = await seed_products(conn, user_ids)
        warehouse_ids = await seed_warehouses(conn)
        await seed_warehouse_inventory(conn, warehouse_ids, products)
        carrier_ids = await seed_carriers(conn)
        await seed_shipping_rates(conn, carrier_ids)
        await seed_coupons(conn)
        await seed_promotions(conn)
        await seed_loyalty_tiers(conn)
        await seed_orders(conn, user_ids, products)
        await seed_reviews(conn, user_ids, products)
        await seed_price_history(conn, products)
        await seed_restock_schedule(conn, warehouse_ids, products)
        await seed_agent_catalog(conn)
        await seed_agent_permissions(conn, user_ids)

        logger.info("Seeding complete!")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
