# Rigol Oscilloscope GUI Controller (PyQt6 Version)

โปรแกรม GUI สำหรับควบคุมเครื่องออสซิลโลสโคป (Oscilloscope) ยี่ห้อ Rigol พัฒนาด้วย **Python 3** และ **PyQt6** สื่อสารผ่านโปรโตคอล SCPI โดยใช้ไลบรารี **PyVISA** รองรับทั้งโหมดเชื่อมต่อเครื่องจริงและโหมดจำลอง (Simulation Mode)

---

## ฟีเจอร์หลัก (Key Features)

* **Dual Mode Operation:**
  * **Simulation Mode:** ทดสอบการทำงานของอินเทอร์เฟซและแสดงคลื่นสัญญาณจำลองโดยไม่ต้องต่อฮาร์ดแวร์จริง
  * **Real Hardware Mode:** เชื่อมต่อกับเครื่อง Rigol จริงผ่าน USB หรือ LAN ด้วย PyVISA
* **Asynchronous Screen Capture:** ดึงภาพหน้าจอผ่าน Background Thread (`QThread`) ป้องกัน GUI ค้างขณะส่งข้อมูล พร้อมบันทึกไฟล์ภาพ (.png)
* **Interactive Control Panel:** ควบคุมการเปิด/ปิด ช่องสัญญาณ CH1 – CH4 และปรับตั้งค่า Volt/Div, Time/Div แบบเรียลไทม์
* **SCPI Command Terminal:** ส่งคำสั่ง SCPI ตรงไปยังเครื่องมือวัด พร้อมปุ่มทางลัดคำสั่งมาตรฐาน (`*IDN?`, `:RUN`, `:STOP`, `:AUToscale`)

---

## ความต้องการของระบบ (Prerequisites)

* **Python:** เวอร์ชัน 3.9 ขึ้นไป
* **Package Manager:** `pip`

### Python Libraries ที่จำเป็น
* `PyQt6` (สำหรับสร้าง GUI)
* `pyvisa` และ `pyvisa-py` (สำหรับสื่อสารผ่านพอร์ต VISA / USB / LAN)
* `Pillow` หรือ `PyQt6` Native Image Handling (สำหรับจัดการภาพถ่ายหน้าจอ)

##  การติดตั้ง Dependencies

```bash
# 1. สร้างและเปิดใช้งาน Virtual Environment (แนะนำ)
python3 -m venv venv
source venv/bin/activate  # สำหรับ Linux / macOS
# venv\Scripts\activate   # สำหรับ Windows

# 2. ติดตั้ง ไลบรารี ทั้งหมด
pip install PyQt6 pyvisa pyvisa-py Pillow
