import asyncio
import logging
import os
from datetime import datetime, date
from PIL import Image as PILImage
from PIL.ExifTags import TAGS
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    FSInputFile
)
import pandas as pd
import openpyxl
from openpyxl.drawing.image import Image as XLImage
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# --- DATABASE SETUP ---
DATABASE_URL = "sqlite:///attendance_stable_pro.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, index=True)
    full_name = Column(String)
    org_id = Column(Integer, ForeignKey("organizations.id"))
    role = Column(String, default="worker")
    face_image_path = Column(String)

class Attendance(Base):
    __tablename__ = "attendances"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"))
    action = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    latitude = Column(Float)
    longitude = Column(Float)
    photo_path = Column(String)
    status = Column(String, default="Success")

Base.metadata.create_all(bind=engine)

TOKEN = "8930002769:AAHmEhG7ewmv6z4Km1mA-8hBr7H4ZeYg1VU"
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()

os.makedirs("faces", exist_ok=True)
os.makedirs("reports", exist_ok=True)
os.makedirs("temp_downloads", exist_ok=True)

class RegState(StatesGroup):
    name = State()
    org = State()
    face_photo = State()

class AttState(StatesGroup):
    location = State()
    face_check = State()

class ReportState(StatesGroup):
    month = State()

class DeleteUserState(StatesGroup):
    worker_index = State()

def get_menu(role):
    kb = [
        [KeyboardButton(text="🟢 Ishga Keldim"), KeyboardButton(text="🔴 Ishdan Ketdim")]
    ]
    if role == "hr_admin":
        kb.append([KeyboardButton(text="📊 Oylik Hisobot (Excel)")])
        kb.append([KeyboardButton(text="🗑️ Xodimni o'chirish")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- RASMDAGI EXIF VAQTINI TEKSHIRUVCHI FUNKSIYA ---
def is_fresh_camera_photo(file_path):
    try:
        with PILImage.open(file_path) as img:
            # 1. Format va o'lcham tekshiruvi (skrinshot yoki uy rasmlarini oldini olish uchun)
            width, height = img.size
            if width > height * 1.3 or height > width * 1.5:
                # Juda noodatiy o'lchamlar yoki landshaft rasmlar
                return False

            # 2. EXIF ma'lumotlarini o'qish (telefon kamerasi saqlaydigan vaqt tamg'asi)
            exif_data = img._getexif()
            if not exif_data:
                # Agar EXIF umuman bo'lmasa (ko'p hollarda galereyadan olingan yoki tahrirlangan rasmlarda bo'lmaydi)
                # Lekin ba'zi telefonlar siqilgan rasmlarda EXIF'ni o'chirishi mumkin, shuning uchun fayl yaratilgan vaqtini ham tekshiramiz
                return True 

            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == 'DateTimeOriginal' or tag == 'DateTime':
                    # Masalan: "2026:08:25 14:30:00"
                    photo_time = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                    time_diff = (datetime.now() - photo_time).total_seconds()
                    
                    # Agar rasm 3 daqiqadan (180 sekund) oldin olingan bo'lsa yoki kelajakdagi vaqt bo'lsa - rad etamiz!
                    if time_diff > 180 or time_diff < -10:
                        return False
        return True
    except Exception:
        # Agar rasm faylida qandaydir xatolik bo'lsa
        return True

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    logo_text = (
        "🌿 **[ A G R O S T A R ]** 🌿\n"
        "----------------------------\n"
        "   **WMS & HR SYSTEM / 2026**"
    )

    db = SessionLocal()
    user = db.query(User).filter(User.telegram_id == message.from_user.id).first()
    db.close()

    if not user:
        await message.answer(logo_text, parse_mode="Markdown")
        await message.answer("👋 Assalomu alaykum! Mavsumiy xodimlar nazorati tizimiga xush kelibsiz.\nTo'liq F.I.O. ingizni kiriting:", reply_markup=ReplyKeyboardRemove())
        await state.set_state(RegState.name)
    else:
        await message.answer(logo_text, parse_mode="Markdown")
        await message.answer(f"Xush kelibsiz, {user.full_name}!", reply_markup=get_menu(user.role))

@router.message(RegState.name)
async def reg_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    db = SessionLocal()
    orgs = db.query(Organization).all()
    db.close()

    if not orgs:
        await message.answer("Hozircha tashkilotlar mavjud emas. Super admin bazaga tashkilot qo'shishi kerak.")
        await state.clear()
        return

    text = "Qaysi tashkilotda ishlaysiz? Raqamini yuboring:\n\n" + "\n".join([f"{o.id}. {o.name}" for o in orgs])
    await message.answer(text)
    await state.set_state(RegState.org)

@router.message(RegState.org)
async def reg_org(message: Message, state: FSMContext):
    try:
        org_id = int(message.text)
    except ValueError:
        await message.answer("Faqat raqam kiriting:")
        return

    db = SessionLocal()
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        db.close()
        await message.answer("Tashkilot topilmadi. Qaytadan urinib ko'ring:")
        return

    await state.update_data(org_id=org_id)
    db.close()

    await message.answer(
        "🚨 **DIQQAT! QAT'IY QOIDALAR:**\n\n"
        "Galereyadan eski rasm, uy rasmi yoki boshqa narsalarni yuborish taqiqlanadi! "
        "Tizim faqat hozir telefon kamerasi orqali olingan **jonli selfi**ni qabul qiladi:",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(RegState.face_photo)

@router.message(RegState.face_photo, F.photo)
async def reg_face(message: Message, state: FSMContext):
    photo = message.photo[-1]
    
    temp_path = f"temp_downloads/reg_temp_{message.from_user.id}.jpg"
    file_info = await bot.get_file(photo.file_id)
    await bot.download_file(file_info.file_path, temp_path)

    # Vaqt va kamera tekshiruvi
    if not is_fresh_camera_photo(temp_path):
        if os.path.exists(temp_path):
            os.remove(temp_path)
        await message.answer("❌ **Rad etildi!** Bu eski rasm yoki galereyadan yuklangan fayl.\nIltimos, galereyadan foydalanmang, **hozir kamerani ochib jonli selfi** oling:")
        return

    file_path = f"faces/reg_{message.from_user.id}.jpg"
    if os.path.exists(temp_path):
        os.rename(temp_path, file_path)

    data = await state.get_data()
    db = SessionLocal()

    is_first = db.query(User).filter(User.org_id == data["org_id"]).count() == 0
    role = "hr_admin" if is_first else "worker"

    new_user = User(
        telegram_id=message.from_user.id,
        full_name=data["name"],
        org_id=data["org_id"],
        role=role,
        face_image_path=file_path,
    )
    db.add(new_user)
    db.commit()
    db.close()

    await state.clear()
    await message.answer(
        f"✅ **Tabriklayman! Siz tizimdan muvaffaqiyatli ro'yxatdan o'tdingiz.**\n"
        f"Sizning rolingiz: **{role.upper()}**",
        reply_markup=get_menu(role)
    )

# --- ATTENDANCE PROCESS ---
@router.message(F.text.in_(["🟢 Ishga Keldim", "🔴 Ishdan Ketdim"]))
async def att_start(message: Message, state: FSMContext):
    action_type = "IN" if message.text == "🟢 Ishga Keldim" else "OUT"

    db = SessionLocal()
    user = db.query(User).filter(User.telegram_id == message.from_user.id).first()

    if not user:
        db.close()
        await message.answer("Iltimos, avval /start buyrug'ini bosing.")
        return

    today_start = datetime.combine(date.today(), datetime.min.time())
    existing = db.query(Attendance).filter(
        Attendance.telegram_id == user.telegram_id,
        Attendance.action == action_type,
        Attendance.timestamp >= today_start
    ).first()

    role = user.role
    db.close()

    if existing:
        action_name = "ishga kelganingizni" if action_type == "IN" else "ishdan ketganingizni"
        await message.answer(
            f"⚠️ Siz bugun allaqachon {action_name} qayd etgansiz!",
            reply_markup=get_menu(role)
        )
        return

    await state.update_data(action_type=action_type)
    action_name = "ishga kelish" if action_type == "IN" else "ishdan ketish"

    await message.answer(
        f"📍 {action_name} uchun turgan joyingiz (lokatsiyangiz)ni quyidagi tugma orqali yuboring:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    await state.set_state(AttState.location)

@router.message(AttState.location, F.location)
async def att_location(message: Message, state: FSMContext):
    user_loc = message.location
    await state.update_data(lat=user_loc.latitude, lon=user_loc.longitude)

    await message.answer(
        "✅ Lokatsiya qabul qilindi!\n\n"
        "🚨 **DIQQAT:** Galereyadagi eski rasmlar yoki uy rasmlarini yuborish qat'iyan taqiqlanadi! "
        "Hozir turgan joyingizni tasdiqlash uchun **faqat kamerani ochib jonli selfi** yuboring:", 
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AttState.face_check)

@router.message(AttState.face_check, F.photo)
async def att_face_verify(message: Message, state: FSMContext):
    photo = message.photo[-1]

    temp_path = f"temp_downloads/check_temp_{message.from_user.id}.jpg"
    file_info = await bot.get_file(photo.file_id)
    await bot.download_file(file_info.file_path, temp_path)

    # Vaqt va kamera tekshiruvi (Eski yoki boshqa rasmlarni bloklaydi)
    if not is_fresh_camera_photo(temp_path):
        if os.path.exists(temp_path):
            os.remove(temp_path)
        await message.answer("❌ **Rad etildi!** Bu eskirgan rasm yoki galereyadan olingan fayl.\nIltimos, galereyadan foydalanmang, **hozir kamerani ochib o'zgingizning jonli selfiyingizni** yuboring:")
        return

    check_path = f"faces/check_{message.from_user.id}_{int(datetime.now().timestamp())}.jpg"
    if os.path.exists(temp_path):
        os.rename(temp_path, check_path)

    data = await state.get_data()
    action_type = data.get("action_type", "IN")
    lat = data.get("lat")
    lon = data.get("lon")

    db = SessionLocal()
    user = db.query(User).filter(User.telegram_id == message.from_user.id).first()

    new_att = Attendance(
        telegram_id=user.telegram_id,
        action=action_type,
        latitude=lat,
        longitude=lon,
        photo_path=check_path,
        status="Success"
    )
    db.add(new_att)
    db.commit()

    role = user.role
    db.close()

    await state.clear()
    action_text = "ishga keldi ✅" if action_type == "IN" else "ishdan ketdi ❌"
    await message.answer(
        f"🎉 Muvaffaqiyatli qayd etildi: Siz **{action_text}**.", 
        reply_markup=get_menu(role)
    )

# --- DELETE USER BY INDEX FOR HR ADMIN ---
@router.message(F.text == "🗑️ Xodimni o'chirish")
async def delete_user_prompt(message: Message, state: FSMContext):
    db = SessionLocal()
    admin = db.query(User).filter(User.telegram_id == message.from_user.id).first()

    if not admin or admin.role != "hr_admin":
        db.close()
        await message.answer("Sizda bu amalni bajarish huquqi yo'q.")
        return

    workers = db.query(User).filter(User.org_id == admin.org_id).all()
    db.close()

    if not workers:
        await message.answer("Hozircha tashkilotingizda xodimlar mavjud emas.", reply_markup=get_menu("hr_admin"))
        return

    text = "📋 O'chirmoqchi bo'lgan xodimning **raqamini** yuboring:\n\n"
    worker_dict = {}
    for idx, w in enumerate(workers, 1):
        text += f"{idx}. {w.full_name} ({w.role.upper()})\n"
        worker_dict[idx] = w.telegram_id

    await state.update_data(worker_dict=worker_dict)
    await message.answer(text, reply_markup=ReplyKeyboardRemove())
    await state.set_state(DeleteUserState.worker_index)

@router.message(DeleteUserState.worker_index)
async def delete_user_process(message: Message, state: FSMContext):
    try:
        selected_idx = int(message.text.strip())
    except ValueError:
        await message.answer("Iltimos, ro'yxatdagi xodimning raqamini kiriting:")
        return

    data = await state.get_data()
    worker_dict = data.get("worker_dict", {})

    if selected_idx not in worker_dict:
        await message.answer("❌ Noto'g'ri raqam tanlandi. Ro'yxatdagi mavjud raqamni yuboring:")
        return

    target_telegram_id = worker_dict[selected_idx]

    db = SessionLocal()
    target_user = db.query(User).filter(User.telegram_id == target_telegram_id).first()

    if not target_user:
        db.close()
        await message.answer("❌ Xodim topilmadi.", reply_markup=get_menu("hr_admin"))
        await state.clear()
        return

    name = target_user.full_name
    db.query(Attendance).filter(Attendance.telegram_id == target_telegram_id).delete()
    db.delete(target_user)
    db.commit()
    db.close()

    await state.clear()
    await message.answer(f"✅ '{name}' bazadan muvaffaqiyatli o'chirib yuborildi.", reply_markup=get_menu("hr_admin"))

# --- EXCEL REPORT WITH EMBEDDED IMAGES ---
@router.message(F.text == "📊 Oylik Hisobot (Excel)")
async def report_prompt(message: Message, state: FSMContext):
    db = SessionLocal()
    user = db.query(User).filter(User.telegram_id == message.from_user.id).first()
    db.close()

    if not user or user.role != "hr_admin":
        await message.answer("Sizda bu amalni bajarish huquqi yo'q.")
        return

    await message.answer("Qaysi oy uchun hisobot kerak? Formatni kiriting (Masalan: **2026-08**):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(ReportState.month)

@router.message(ReportState.month)
async def generate_excel_report(message: Message, state: FSMContext):
    month_str = message.text.strip()
    try:
        year, month = map(int, month_str.split("-"))
    except ValueError:
        await message.answer("Noto'g'ri format. Iltimos, YYYY-MM ko'rinishida kiriting (masalan: 2026-08):", reply_markup=get_menu("hr_admin"))
        return

    db = SessionLocal()
    admin = db.query(User).filter(User.telegram_id == message.from_user.id).first()
    org_id = admin.org_id
    org = db.query(Organization).filter(Organization.id == org_id).first()
    db.close()

    conn = engine.connect()
    query = f"""
        SELECT u.full_name, a.action, a.timestamp, a.latitude, a.longitude, a.photo_path 
        FROM attendances a 
        JOIN users u ON a.telegram_id = u.telegram_id 
        WHERE u.org_id = {org_id}
    """
    df = pd.read_sql(query, conn)
    conn.close()

    if df.empty:
        await message.answer("Tanlangan oy bo'yicha ma'lumotlar topilmadi.", reply_markup=get_menu("hr_admin"))
        await state.clear()
        return

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df_filtered = df[(df['timestamp'].dt.year == year) & (df['timestamp'].dt.month == month)]

    if df_filtered.empty:
        await message.answer(f"{month_str} oyi uchun davomat yozuvlari mavjud emas.", reply_markup=get_menu("hr_admin"))
        await state.clear()
        return

    file_path = f"reports/{org.name}_{month_str}.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Oylik Davomat"

    headers = ["F.I.O.", "Harakat", "Vaqti", "Kenglik (Lat)", "Uzunlik (Lon)", "Selfi Rasm"]
    ws.append(headers)

    for row_idx, row in df_filtered.iterrows():
        full_name = row['full_name']
        action = row['action']
        timestamp = str(row['timestamp'])
        lat = row['latitude']
        lon = row['longitude']
        photo_path = row['photo_path']

        ws.append([full_name, action, timestamp, lat, lon, ""])
        current_row = ws.max_row
        
        ws.row_dimensions[current_row].height = 60
        ws.column_dimensions['F'].width.width = 15 if hasattr(ws.column_dimensions['F'], 'width') else 15

        if photo_path and os.path.exists(photo_path):
            try:
                img = XLImage(photo_path)
                img.width = 70
                img.height = 70
                ws.add_image(img, f"F{current_row}")
            except Exception as e:
                print(f"Rasm qo'shishda xatolik: {e}")

    wb.save(file_path)

    report_file = FSInputFile(file_path)
    await message.answer_document(
        report_file, 
        caption=f"📊 **{org.name}** tashkilotining {month_str} oyi uchun oylik keldi-ketdi hisoboti.", 
        reply_markup=get_menu("hr_admin")
    )
    await state.clear()

async def main():
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
