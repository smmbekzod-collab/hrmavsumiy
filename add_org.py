from hr import SessionLocal, Organization

# Yangi tashkilot qo'shish (Faqat nomining o'zi yetarli)
db = SessionLocal()

new_org = Organization(
    name="Oltiariq Agrostar №1"
)

db.add(new_org)
db.commit()
db.close()

print("✅ Tashkilot muvaffaqiyatli qo'shildi! Endi botni bemalol ishga tushirishingiz mumkin.")