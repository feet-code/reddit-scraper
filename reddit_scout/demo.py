"""Clearly synthetic examples; never write these to the real collection database."""
from .models import Item


def demo_items() -> list[Item]:
    rows = [
        ("t3_demo01", "smallbusiness", "Chasing overdue invoices every week",
         "I run a design agency. Chasing overdue invoices is a nightmare. I waste 5 hours every week copying reminders from a spreadsheet. I would pay $150 per month to fix this."),
        ("t3_demo02", "freelance", "Late invoice follow-up is taking over my Fridays",
         "My business has 25 clients. I spend 3 hours every week on unpaid invoice follow-up. We currently pay $80 per month for QuickBooks but still track reminders manually."),
        ("t3_demo03", "smallbusiness", "Unpaid invoices and manual reminders",
         "Our team keeps chasing overdue invoices manually. We lose 4 hours each week digging through email. I am looking for a tool that tracks replies and stops reminders when an invoice is paid."),
        ("t3_demo04", "shopify", "Inventory sync keeps failing across stores",
         "I run an online store. Inventory sync is unreliable and we keep overselling. We are paying $200 a month for a connector and still checking a spreadsheet every day."),
        ("t3_demo05", "ecommerce", "Manual inventory sync is a nightmare",
         "Our store has stock mismatches between channels. Manual inventory sync takes 2 hours daily. I would pay $250 per month for reliable discrepancy alerts."),
        ("t3_demo06", "marketing", "Manual reporting takes forever",
         "Our agency creates reports for clients every month. Manual reporting takes 8 hours with Excel and Google Sheets. We currently pay $99 per month for dashboards but still copy tables by hand."),
        ("t3_demo07", "ppc", "Client reports still need manual edits",
         "My team wastes 6 hours every week on manual reports. I need a tool to validate campaign numbers before sending a report to clients."),
        ("t3_demo08", "smallbusiness", "An annoying color setting",
         "I hate that my calendar does not support custom colors. I won't pay for this though. I am looking for a free alternative."),
        ("t3_demo09", "entrepreneur", "I launched an invoice tool",
         "I built an invoice follow-up tool for overdue invoices. Check out my product and book a demo. Stop wasting hours and sign up today!"),
    ]
    results = [Item(id, id, sub, "post", title, body, "", source="demo", complete=True)
               for id, sub, title, body in rows]
    results.append(Item("t1_demo10", "t3_demo01", "smallbusiness", "comment", rows[0][2],
                        "Our business also has overdue invoice follow-up problems. We keep chasing unpaid invoices every week and I would pay for a reliable fix.",
                        "", source="demo", complete=True))
    results.append(Item("t1_demo11", "t3_demo01", "smallbusiness", "comment", rows[0][2],
                        "I won't pay for automated overdue invoice follow-up. Free reminders are enough for my business.",
                        "", source="demo", complete=True))
    return results
