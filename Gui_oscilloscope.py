from __future__ import annotations

import sys
import time
from typing import Optional, List

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QComboBox,
    QCheckBox, QTextEdit, QFileDialog, QMessageBox, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QFont, QColor

try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False


# ==============================================================================
# MODEL LAYER (SCPI & VISA Communication)
# ==============================================================================

class InstrumentError(Exception):
    """เกิดข้อผิดพลาดระหว่างสื่อสารกับเครื่องมือ"""


class RigolInstrument:
    IDN_QUERY = "*IDN?"
    DEFAULT_TIMEOUT_MS = 5000
    CAPTURE_TIMEOUT_MS = 30000  

    def __init__(self):
        self._rm: Optional["pyvisa.ResourceManager"] = None
        self._inst = None
        self.resource_name: Optional[str] = None
        self.idn: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self._inst is not None

    def list_resources(self) -> List[str]:
        if not PYVISA_AVAILABLE:
            return []
        if self._rm is None:
            self._rm = pyvisa.ResourceManager()
        try:
            return list(self._rm.list_resources())
        except Exception as exc:
            raise InstrumentError(f"ไม่สามารถค้นหา resource ได้: {exc}") from exc

    def connect(self, resource_name: str, timeout_ms: Optional[int] = None) -> str:
        if not PYVISA_AVAILABLE:
            raise InstrumentError("ยังไม่ได้ติดตั้ง pyvisa (pip install pyvisa pyvisa-py)")
        if self._rm is None:
            self._rm = pyvisa.ResourceManager()
        try:
            self._inst = self._rm.open_resource(resource_name)
            self._inst.timeout = timeout_ms or self.DEFAULT_TIMEOUT_MS
            self._inst.read_termination = "\n"
            self._inst.write_termination = "\n"
            try:
                self._inst.chunk_size = 1024 * 1024
            except Exception:
                pass  
            self.resource_name = resource_name
            self.idn = self._inst.query(self.IDN_QUERY).strip()
            return self.idn
        except Exception as exc:
            self._inst = None
            raise InstrumentError(f"เชื่อมต่อไม่สำเร็จ: {exc}") from exc

    def disconnect(self) -> None:
        if self._inst is not None:
            try:
                self._inst.close()
            finally:
                self._inst = None
                self.resource_name = None
                self.idn = None

    def _require_connection(self):
        if not self.is_connected:
            raise InstrumentError("ยังไม่ได้เชื่อมต่อกับเครื่องมือ")

    def write(self, command: str) -> None:
        self._require_connection()
        try:
            self._inst.write(command)
        except Exception as exc:
            raise InstrumentError(f"ส่งคำสั่งล้มเหลว ({command}): {exc}") from exc

    def query(self, command: str) -> str:
        self._require_connection()
        try:
            return self._inst.query(command).strip()
        except Exception as exc:
            raise InstrumentError(f"สอบถามล้มเหลว ({command}): {exc}") from exc

    def send(self, command: str) -> str:
        command = command.strip()
        if not command:
            return ""
        if command.endswith("?"):
            return self.query(command)
        self.write(command)
        return ""

    def capture_screenshot_png(self) -> bytes:
        self._require_connection()
        original_timeout = self._inst.timeout
        try:
            self._inst.timeout = self.CAPTURE_TIMEOUT_MS
            self._inst.write(":DISP:DATA? ON,PNG")

            header = self._inst.read_bytes(2)
            if header[0:1] != b"#":
                raise InstrumentError(f"Header ไม่ถูกต้อง: {header!r}")
            num_digits = int(header[1:2])
            if num_digits == 0:
                raise InstrumentError("Indefinite length block ไม่รองรับ")

            length = int(self._inst.read_bytes(num_digits).decode())
            image_data = self._inst.read_bytes(length)
            try:
                self._inst.read_bytes(1)  
            except Exception:
                pass

            return image_data
        except InstrumentError:
            raise
        except Exception as exc:
            raise InstrumentError(f"ดึงภาพหน้าจอไม่สำเร็จ: {exc}") from exc
        finally:
            self._inst.timeout = original_timeout

    def set_channel_status(self, channel: int, enable: bool) -> None:
        self.write(f":CHANnel{channel}:DISPlay {'ON' if enable else 'OFF'}")

    def get_channel_status(self, channel: int) -> bool:
        res = self.query(f":CHANnel{channel}:DISPlay?")
        return res == "1"
    
    def set_volt_scale(self, channel: int, scale_val: float) -> None:
        self.write(f":CHANnel{channel}:SCALe {scale_val}")

    def get_volt_scale(self, channel: int) -> float:
        return float(self.query(f":CHANnel{channel}:SCALe?"))

    def set_time_scale(self, scale_val: float) -> None:
        self.write(f":TIMebase:MAIN:SCALe {scale_val}")

    def get_time_scale(self) -> float:
        return float(self.query(":TIMebase:MAIN:SCALe?"))

    def set_trigger_source(self, source: str) -> None:
        self.write(f":TRIGger:EDGe:SOURce {source}")

    def get_trigger_source(self) -> str:
        return self.query(":TRIGger:EDGe:SOURce?")

    def set_trigger_sweep(self, sweep: str) -> None:
        self.write(f":TRIGger:SWEep {sweep}")

    def get_trigger_sweep(self) -> str:
        return self.query(":TRIGger:SWEep?")

    def set_trigger_level(self, level: float) -> None:
        self.write(f":TRIGger:EDGe:LEVel {level}")

    def get_trigger_level(self) -> float:
        return float(self.query(":TRIGger:EDGe:LEVel?"))

    def get_measurement(self, item: str, channel: int) -> float:
        res = self.query(f":MEASure:ITEM? {item},CHANnel{channel}")
        return float(res)


class SimulatedInstrument(RigolInstrument):
    def __init__(self):
        super().__init__()
        self._sim_channels = {1: True, 2: False, 3: False, 4: False}
        self._sim_volt_scales = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}
        self._sim_time_scale = 0.0005
        self._sim_trig_source = "CHANnel1"
        self._sim_trig_sweep = "AUTO"
        self._sim_trig_level = 0.0

    def list_resources(self) -> List[str]:
        return ["SIM::MSO1104::INSTR"]

    def connect(self, resource_name: str, timeout_ms: Optional[int] = None) -> str:
        time.sleep(0.3)
        self.resource_name = resource_name
        self.idn = "RIGOL TECHNOLOGIES,MSO1104,SIMULATED,00.01.03 (PyQt6 Simulation)"
        self._inst = "SIMULATED"
        return self.idn

    def disconnect(self) -> None:
        self._inst = None
        self.resource_name = None
        self.idn = None

    def write(self, command: str) -> None:
        self._require_connection()

    def query(self, command: str) -> str:
        self._require_connection()
        if command == self.IDN_QUERY:
            return self.idn
        return f"<simulated response for '{command}'>"

    def capture_screenshot_png(self) -> bytes:
        self._require_connection()
        time.sleep(0.5)
        # ส่งค่าจำลองโปร่งใสหรือภาพเปล่า
        from PyQt6.QtGui import QImage, QPainter, QColor
        from PyQt6.QtCore import QBuffer, QIODevice
        
        img = QImage(400, 240, QImage.Format.Format_RGB32)
        img.fill(QColor(20, 20, 30))
        painter = QPainter(img)
        painter.setPen(QColor(0, 255, 128))
        painter.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, "SIMULATED SCREENSHOT")
        painter.end()

        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buffer, "PNG")
        return buffer.data().data()

    def set_channel_status(self, channel: int, enable: bool) -> None:
        self._require_connection()
        self._sim_channels[channel] = enable

    def get_channel_status(self, channel: int) -> bool:
        self._require_connection()
        return self._sim_channels.get(channel, False)

    def set_volt_scale(self, channel: int, scale_val: float) -> None:
        self._require_connection()
        self._sim_volt_scales[channel] = scale_val

    def get_volt_scale(self, channel: int) -> float:
        self._require_connection()
        return self._sim_volt_scales.get(channel, 1.0)

    def set_time_scale(self, scale_val: float) -> None:
        self._require_connection()
        self._sim_time_scale = scale_val

    def get_time_scale(self) -> float:
        self._require_connection()
        return self._sim_time_scale

    def set_trigger_source(self, source: str) -> None:
        self._require_connection()
        self._sim_trig_source = source

    def get_trigger_source(self) -> str:
        self._require_connection()
        return self._sim_trig_source

    def set_trigger_sweep(self, sweep: str) -> None:
        self._require_connection()
        self._sim_trig_sweep = sweep

    def get_trigger_sweep(self) -> str:
        self._require_connection()
        return self._sim_trig_sweep

    def set_trigger_level(self, level: float) -> None:
        self._require_connection()
        self._sim_trig_level = level

    def get_trigger_level(self) -> float:
        self._require_connection()
        return self._sim_trig_level

    def get_measurement(self, item: str, channel: int) -> float:
        self._require_connection()
        import random
        if item == "VPP":
            return round(2.5 + random.uniform(-0.05, 0.05), 3)
        elif item == "VMAX":
            return round(1.25 + random.uniform(-0.02, 0.02), 3)
        elif item == "VMIN":
            return round(-1.25 + random.uniform(-0.02, 0.02), 3)
        elif item == "FREQuency":
            return round(1000.0 + random.uniform(-5.0, 5.0), 1)
        elif item == "PERiod":
            return round(0.001 + random.uniform(-0.00001, 0.00001), 6)
        return 0.0


# ==============================================================================
# PYQT6 THREADING (Worker for Image Capture)
# ==============================================================================

class CaptureThread(QThread):
    finished = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    def __init__(self, instrument: RigolInstrument):
        super().__init__()
        self.instrument = instrument

    def run(self):
        try:
            data = self.instrument.capture_screenshot_png()
            self.finished.emit(data)
        except Exception as exc:
            self.failed.emit(str(exc))


# ==============================================================================
# VIEW LAYER (PyQt6 Widgets & UI Controls)
# ==============================================================================

class ConnectionGroup(QGroupBox):
    def __init__(self, parent, on_refresh, on_connect, on_disconnect):
        super().__init__("Instrument Connection")
        self.on_refresh = on_refresh
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect

        layout = QGridLayout(self)

        layout.addWidget(QLabel("VISA Resource:"), 0, 0)
        self.resource_combo = QComboBox()
        self.resource_combo.setEditable(True)
        layout.addWidget(self.resource_combo, 0, 1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self._refresh)
        layout.addWidget(self.btn_refresh, 0, 2)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self._connect)
        layout.addWidget(self.btn_connect, 0, 3)

        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.clicked.connect(self._disconnect)
        layout.addWidget(self.btn_disconnect, 0, 4)

        self.lbl_status = QLabel("Status: Disconnected")
        self.lbl_status.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self.lbl_status, 1, 0, 1, 5)

    def _refresh(self):
        resources = self.on_refresh()
        self.resource_combo.clear()
        self.resource_combo.addItems(resources)

    def _connect(self):
        self.on_connect(self.resource_combo.currentText())

    def _disconnect(self):
        self.on_disconnect()

    def set_connected_state(self, connected: bool, idn: str = ""):
        if connected:
            self.lbl_status.setText(f"Status: Connected -> {idn}")
            self.lbl_status.setStyleSheet("color: green; font-weight: bold;")
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.resource_combo.setEnabled(False)
        else:
            self.lbl_status.setText("Status: Disconnected")
            self.lbl_status.setStyleSheet("color: red; font-weight: bold;")
            self.btn_connect.setEnabled(True)
            self.btn_disconnect.setEnabled(False)
            self.resource_combo.setEnabled(True)


class CommandGroup(QGroupBox):
    def __init__(self, parent, on_send):
        super().__init__("SCPI Command")
        self.on_send = on_send

        layout = QVBoxLayout(self)

        input_layout = QHBoxLayout()
        self.cmd_input = QLineEdit("*IDN?")
        self.cmd_input.returnPressed.connect(self._send)
        input_layout.addWidget(self.cmd_input)

        btn_send = QPushButton("Send")
        btn_send.clicked.connect(self._send)
        input_layout.addWidget(btn_send)

        layout.addLayout(input_layout)

        shortcut_layout = QHBoxLayout()
        shortcuts = [("*IDN?", "*IDN?"), ("Run", ":RUN"), ("Stop", ":STOP"), 
                     ("Auto Scale", ":AUToscale"), ("Single", ":SINGle")]
        for label, cmd in shortcuts:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, c=cmd: self._send(c))
            shortcut_layout.addWidget(btn)

        layout.addLayout(shortcut_layout)

    def _send(self, forced_command: Optional[str] = None):
        cmd = forced_command if forced_command is not None else self.cmd_input.text()
        self.on_send(cmd)


class ChannelControlGroup(QGroupBox):
    def __init__(self, parent, on_toggle):
        super().__init__("Channel View Control")
        self.on_toggle = on_toggle

        layout = QVBoxLayout(self)
        self.ch_colors = {
            1: "#D4AF37", 2: "#00A8E8", 3: "#DE3163", 4: "#2E8B57"
        }
        self.checkboxes = {}

        for ch in range(1, 5):
            h_layout = QHBoxLayout()
            lbl = QLabel(f"CH {ch}")
            lbl.setStyleSheet(f"color: {self.ch_colors[ch]}; font-weight: bold; font-size: 12px;")
            h_layout.addWidget(lbl)

            cb = QCheckBox("OFF")
            cb.clicked.connect(lambda checked, c=ch: self._on_click(c, checked))
            h_layout.addWidget(cb)
            
            self.checkboxes[ch] = cb
            layout.addLayout(h_layout)

    def _on_click(self, ch: int, checked: bool):
        self.checkboxes[ch].setText("ON" if checked else "OFF")
        self.on_toggle(ch, checked)

    def update_ui_state(self, ch: int, is_on: bool):
        self.checkboxes[ch].blockSignals(True)
        self.checkboxes[ch].setChecked(is_on)
        self.checkboxes[ch].setText("ON" if is_on else "OFF")
        self.checkboxes[ch].blockSignals(False)


class ScaleControlGroup(QGroupBox):
    def __init__(self, parent, on_volt_change, on_time_change):
        super().__init__("Scale Control")
        self.on_volt_change = on_volt_change
        self.on_time_change = on_time_change

        self.volt_presets = [
            (0.001, "1 mV"), (0.002, "2 mV"), (0.005, "5 mV"),
            (0.01, "10 mV"), (0.02, "20 mV"), (0.05, "50 mV"),
            (0.1, "100 mV"), (0.2, "200 mV"), (0.5, "500 mV"),
            (1.0, "1 V"), (2.0, "2 V"), (5.0, "5 V"), (10.0, "10 V")
        ]
        self.time_presets = [
            (5e-9, "5 ns"), (10e-9, "10 ns"), (20e-9, "20 ns"), (50e-9, "50 ns"),
            (100e-9, "100 ns"), (200e-9, "200 ns"), (500e-9, "500 ns"),
            (1e-6, "1 us"), (2e-6, "2 us"), (5e-6, "5 us"), (10e-6, "10 us"),
            (20e-6, "20 us"), (50e-6, "50 us"), (100e-6, "100 us"), (200e-6, "200 us"),
            (500e-6, "500 us"), (1e-3, "1 ms"), (2e-3, "2 ms"), (5e-3, "5 ms"),
            (10e-3, "10 ms"), (20e-3, "20 ms"), (50e-3, "50 ms"), (100e-3, "100 ms"),
            (200e-3, "200 ms"), (500e-3, "500 ms"), (1.0, "1 s"), (2.0, "2 s"),
            (5.0, "5 s"), (10.0, "10 s"), (20.0, "20 s"), (50.0, "50 s")
        ]

        layout = QVBoxLayout(self)

        # Vertical
        v_box = QGroupBox("Vertical Scale (Volt/Div)")
        v_layout = QGridLayout(v_box)
        v_layout.addWidget(QLabel("Channel:"), 0, 0)
        self.ch_select = QComboBox()
        self.ch_select.addItems(["CH1", "CH2", "CH3", "CH4"])
        self.ch_select.currentIndexChanged.connect(self._on_channel_select)
        v_layout.addWidget(self.ch_select, 0, 1, 1, 3)

        v_layout.addWidget(QLabel("Scale:"), 1, 0)
        btn_v_dec = QPushButton("-")
        btn_v_dec.clicked.connect(lambda: self._step("volt", -1))
        v_layout.addWidget(btn_v_dec, 1, 1)

        self.volt_combo = QComboBox()
        self.volt_combo.addItems([s for _, s in self.volt_presets])
        self.volt_combo.currentIndexChanged.connect(lambda: self._on_combo_select("volt"))
        v_layout.addWidget(self.volt_combo, 1, 2)

        btn_v_inc = QPushButton("+")
        btn_v_inc.clicked.connect(lambda: self._step("volt", 1))
        v_layout.addWidget(btn_v_inc, 1, 3)
        layout.addWidget(v_box)

        # Horizontal
        h_box = QGroupBox("Horizontal Scale (Time/Div)")
        h_layout = QGridLayout(h_box)
        h_layout.addWidget(QLabel("Scale:"), 0, 0)
        btn_t_dec = QPushButton("-")
        btn_t_dec.clicked.connect(lambda: self._step("time", -1))
        h_layout.addWidget(btn_t_dec, 0, 1)

        self.time_combo = QComboBox()
        self.time_combo.addItems([s for _, s in self.time_presets])
        self.time_combo.currentIndexChanged.connect(lambda: self._on_combo_select("time"))
        h_layout.addWidget(self.time_combo, 0, 2)

        btn_t_inc = QPushButton("+")
        btn_t_inc.clicked.connect(lambda: self._step("time", 1))
        h_layout.addWidget(btn_t_inc, 0, 3)
        layout.addWidget(h_box)

    def _get_closest_index(self, val: float, presets: list) -> int:
        return min(range(len(presets)), key=lambda i: abs(presets[i][0] - val))

    def set_volt_ui(self, scale_val: float):
        idx = self._get_closest_index(scale_val, self.volt_presets)
        self.volt_combo.blockSignals(True)
        self.volt_combo.setCurrentIndex(idx)
        self.volt_combo.blockSignals(False)

    def set_time_ui(self, scale_val: float):
        idx = self._get_closest_index(scale_val, self.time_presets)
        self.time_combo.blockSignals(True)
        self.time_combo.setCurrentIndex(idx)
        self.time_combo.blockSignals(False)

    def _on_channel_select(self):
        ch = int(self.ch_select.currentText()[-1])
        self.on_volt_change(ch, None, is_channel_switch=True)

    def _on_combo_select(self, axis: str):
        if axis == "volt":
            val = self.volt_presets[self.volt_combo.currentIndex()][0]
            ch = int(self.ch_select.currentText()[-1])
            self.on_volt_change(ch, val)
        else:
            val = self.time_presets[self.time_combo.currentIndex()][0]
            self.on_time_change(val)

    def _step(self, axis: str, step: int):
        combo = self.volt_combo if axis == "volt" else self.time_combo
        new_idx = max(0, min(combo.count() - 1, combo.currentIndex() + step))
        combo.setCurrentIndex(new_idx)


class TriggerMeasureGroup(QGroupBox):
    def __init__(self, parent, on_trigger_change, on_measure):
        super().__init__("Trigger & Measure")
        self.on_trigger_change = on_trigger_change
        self.on_measure = on_measure

        layout = QVBoxLayout(self)

        # Trigger Settings
        t_box = QGroupBox("Trigger Settings")
        t_layout = QGridLayout(t_box)

        t_layout.addWidget(QLabel("Source:"), 0, 0)
        self.trig_src_combo = QComboBox()
        self.trig_src_combo.addItems(["CH1", "CH2", "CH3", "CH4"])
        self.trig_src_combo.currentIndexChanged.connect(self._on_trigger_update)
        t_layout.addWidget(self.trig_src_combo, 0, 1)

        t_layout.addWidget(QLabel("Sweep:"), 1, 0)
        self.trig_sweep_combo = QComboBox()
        self.trig_sweep_combo.addItems(["AUTO", "NORMAL", "SINGLE"])
        self.trig_sweep_combo.currentIndexChanged.connect(self._on_trigger_update)
        t_layout.addWidget(self.trig_sweep_combo, 1, 1)

        t_layout.addWidget(QLabel("Level (V):"), 2, 0)
        self.trig_level_input = QLineEdit("0.0")
        self.trig_level_input.returnPressed.connect(self._on_trigger_update)
        t_layout.addWidget(self.trig_level_input, 2, 1)

        btn_set_trig = QPushButton("Set")
        btn_set_trig.clicked.connect(self._on_trigger_update)
        t_layout.addWidget(btn_set_trig, 2, 2)
        layout.addWidget(t_box)

        # Auto Measurement
        m_box = QGroupBox("Auto Measurement")
        m_layout = QGridLayout(m_box)

        m_layout.addWidget(QLabel("Type:"), 0, 0)
        self.meas_item_combo = QComboBox()
        self.meas_item_combo.addItems(["VPP", "VMAX", "VMIN", "FREQuency", "PERiod"])
        m_layout.addWidget(self.meas_item_combo, 0, 1)

        m_layout.addWidget(QLabel("Channel:"), 1, 0)
        self.meas_ch_combo = QComboBox()
        self.meas_ch_combo.addItems(["CH1", "CH2", "CH3", "CH4"])
        m_layout.addWidget(self.meas_ch_combo, 1, 1)

        btn_measure = QPushButton("Measure Now")
        btn_measure.clicked.connect(self._on_measure_click)
        m_layout.addWidget(btn_measure, 2, 0, 1, 2)

        self.lbl_result = QLabel("Value: ---")
        self.lbl_result.setStyleSheet("color: #007ACC; font-weight: bold; font-size: 13px;")
        m_layout.addWidget(self.lbl_result, 3, 0, 1, 2)
        layout.addWidget(m_box)

    def set_trigger_ui(self, source: str, sweep: str, level: float):
        ch_map = {"CHANnel1": "CH1", "CHANnel2": "CH2", "CHANnel3": "CH3", "CHANnel4": "CH4"}
        src = ch_map.get(source, "CH1")
        
        self.trig_src_combo.blockSignals(True)
        self.trig_sweep_combo.blockSignals(True)
        self.trig_src_combo.setCurrentText(src)
        self.trig_sweep_combo.setCurrentText(sweep.upper())
        self.trig_level_input.setText(f"{level:.3f}")
        self.trig_src_combo.blockSignals(False)
        self.trig_sweep_combo.blockSignals(False)

    def show_measure_result(self, value: float, item: str):
        if value > 1e10 or value < -1e10:
            self.lbl_result.setText("Value: Error/No Sig")
            return

        units = {"VPP": "V", "VMAX": "V", "VMIN": "V", "FREQuency": "Hz", "PERiod": "s"}
        unit = units.get(item, "")

        if item == "FREQuency" and value >= 1000.0:
            self.lbl_result.setText(f"Value: {value/1000.0:.3f} kHz")
        elif item == "PERiod" and value < 0.001:
            self.lbl_result.setText(f"Value: {value*1e6:.2f} us")
        else:
            self.lbl_result.setText(f"Value: {value:.4f} {unit}")

    def _on_trigger_update(self):
        src_val = f"CHANnel{self.trig_src_combo.currentText()[-1]}"
        sweep_val = self.trig_sweep_combo.currentText()
        if sweep_val == "NORMAL":
            sweep_val = "NORMal"
        elif sweep_val == "SINGLE":
            sweep_val = "SINGle"

        try:
            level_val = float(self.trig_level_input.text())
        except ValueError:
            QMessageBox.critical(self, "ค่าไม่ถูกต้อง", "กรุณากรอกระดับแรงดัน Trigger Level เป็นตัวเลขที่ถูกต้อง")
            return

        self.on_trigger_change(src_val, sweep_val, level_val)

    def _on_measure_click(self):
        item = self.meas_item_combo.currentText()
        ch = int(self.meas_ch_combo.currentText()[-1])
        self.on_measure(item, ch)


class CaptureGroup(QGroupBox):
    def __init__(self, parent, on_capture, on_save):
        super().__init__("Oscilloscope Screen")
        self.on_capture = on_capture
        self.on_save = on_save

        layout = QVBoxLayout(self)

        btn_layout = QHBoxLayout()
        self.btn_capture = QPushButton("Capture")
        self.btn_capture.clicked.connect(self.on_capture)
        btn_layout.addWidget(self.btn_capture)

        self.btn_save = QPushButton("Save Image...")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.on_save)
        btn_layout.addWidget(self.btn_save)
        layout.addLayout(btn_layout)

        self.img_label = QLabel("No image")
        self.img_label.setFixedSize(450, 315)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_label.setStyleSheet("background-color: #202020; color: white; border: 1px solid #404040;")
        layout.addWidget(self.img_label)

    def set_capturing_state(self, capturing: bool):
        if capturing:
            self.btn_capture.setEnabled(False)
            self.btn_capture.setText("Capturing...")
            self.img_label.setText("กำลังดึงภาพจากเครื่อง...")
        else:
            self.btn_capture.setEnabled(True)
            self.btn_capture.setText("Capture")

    def show_image(self, png_bytes: bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(png_bytes)
        scaled_pixmap = pixmap.scaled(
            self.img_label.size(), 
            Qt.AspectRatioMode.KeepAspectRatio, 
            Qt.TransformationMode.SmoothTransformation
        )
        self.img_label.setPixmap(scaled_pixmap)
        self.btn_save.setEnabled(True)


class LogGroup(QGroupBox):
    def __init__(self, parent):
        super().__init__("Response Log")
        layout = QVBoxLayout(self)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

    def log(self, message: str, tag: str = "info"):
        timestamp = time.strftime("%H:%M:%S")
        prefix = {"info": "", "error": "[ERROR] ", "cmd": ">>> "}.get(tag, "")
        color = {"info": "black", "error": "red", "cmd": "blue"}.get(tag, "black")
        
        formatted_msg = f'<font color="{color}">[{timestamp}] {prefix}{message}</font>'
        self.log_text.append(formatted_msg)


# ==============================================================================
# CONTROLLER LAYER (Main Window Application)
# ==============================================================================

class OscilloscopeApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rigol MSO1104 Oscilloscope GUI (PyQt6)")
        self.resize(1200, 780)

        self.instrument: RigolInstrument = (
            RigolInstrument() if PYVISA_AVAILABLE else SimulatedInstrument()
        )
        self._last_capture: Optional[bytes] = None

        self._build_layout()

        if not PYVISA_AVAILABLE:
            self.log_group.log(
                "ไม่พบไลบรารี pyvisa จึงเริ่มโปรแกรมใน Simulation Mode "
                "(pip install pyvisa pyvisa-py เพื่อใช้งานกับเครื่องจริง)",
                tag="error",
            )

    def _build_layout(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.conn_group = ConnectionGroup(
            self,
            on_refresh=self._handle_refresh,
            on_connect=self._handle_connect,
            on_disconnect=self._handle_disconnect
        )
        main_layout.addWidget(self.conn_group)

        self.cmd_group = CommandGroup(self, on_send=self._handle_send)
        main_layout.addWidget(self.cmd_group)

        # Middle Panels
        middle_layout = QHBoxLayout()

        self.capture_group = CaptureGroup(
            self, on_capture=self._handle_capture, on_save=self._handle_save_image
        )
        middle_layout.addWidget(self.capture_group)

        self.channel_group = ChannelControlGroup(
            self, on_toggle=self._handle_channel_toggle
        )
        middle_layout.addWidget(self.channel_group)

        self.scale_group = ScaleControlGroup(
            self, on_volt_change=self._handle_volt_change, on_time_change=self._handle_time_change
        )
        middle_layout.addWidget(self.scale_group)

        self.trig_meas_group = TriggerMeasureGroup(
            self, on_trigger_change=self._handle_trigger_change, on_measure=self._handle_measure
        )
        middle_layout.addWidget(self.trig_meas_group)

        main_layout.addLayout(middle_layout)

        self.log_group = LogGroup(self)
        main_layout.addWidget(self.log_group)

    # Connection Handlers
    def _handle_refresh(self) -> List[str]:
        try:
            resources = self.instrument.list_resources()
            self.log_group.log(f"พบ resource ทั้งหมด {len(resources)} รายการ")
            return resources
        except InstrumentError as exc:
            self.log_group.log(str(exc), tag="error")
            return []

    def _handle_connect(self, resource_name: str):
        if not resource_name:
            QMessageBox.warning(self, "ไม่ได้เลือก Resource", "กรุณาเลือกหรือพิมพ์ VISA resource ก่อน")
            return
        try:
            idn = self.instrument.connect(resource_name)
            self.conn_group.set_connected_state(True, idn)
            self.log_group.log(f"เชื่อมต่อสำเร็จ: {idn}")

            # ดึงสถานะ Channel 1-4
            for ch in range(1, 5):
                try:
                    is_on = self.instrument.get_channel_status(ch)
                    self.channel_group.update_ui_state(ch, is_on)
                except Exception:
                    pass

            # ดึงค่า Volt/Time scale
            try:
                ch = int(self.scale_group.ch_select.currentText()[-1])
                self.scale_group.set_volt_ui(self.instrument.get_volt_scale(ch))
                self.scale_group.set_time_ui(self.instrument.get_time_scale())
            except Exception as exc:
                self.log_group.log(f"ดึงค่าสเกลเริ่มต้นล้มเหลว: {exc}", tag="error")

            # ดึงข้อมูล Trigger
            try:
                t_src = self.instrument.get_trigger_source()
                t_sweep = self.instrument.get_trigger_sweep()
                t_level = self.instrument.get_trigger_level()
                self.trig_meas_group.set_trigger_ui(t_src, t_sweep, t_level)
            except Exception as exc:
                self.log_group.log(f"ดึงค่าข้อมูล Trigger เริ่มต้นล้มเหลว: {exc}", tag="error")

        except InstrumentError as exc:
            self.conn_group.set_connected_state(False)
            self.log_group.log(str(exc), tag="error")
            QMessageBox.critical(self, "เชื่อมต่อไม่สำเร็จ", str(exc))

    def _handle_disconnect(self):
        self.instrument.disconnect()
        self.conn_group.set_connected_state(False)
        self.log_group.log("ตัดการเชื่อมต่อแล้ว")

    # Command Handler
    def _handle_send(self, command: str):
        if not self.instrument.is_connected:
            QMessageBox.warning(self, "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อเครื่องมือก่อนส่งคำสั่ง")
            return
        self.log_group.log(command, tag="cmd")
        try:
            result = self.instrument.send(command)
            if result:
                self.log_group.log(result)
        except InstrumentError as exc:
            self.log_group.log(str(exc), tag="error")

    # Capture Handler (QThread)
    def _handle_capture(self):
        if not self.instrument.is_connected:
            QMessageBox.warning(self, "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อเครื่องมือก่อนจับภาพหน้าจอ")
            return

        self.log_group.log("กำลังจับภาพหน้าจอ... (อาจใช้เวลาสักครู่ กรุณารอ)")
        self.capture_group.set_capturing_state(True)

        self.capture_thread = CaptureThread(self.instrument)
        self.capture_thread.finished.connect(self._on_capture_success)
        self.capture_thread.failed.connect(self._on_capture_error)
        self.capture_thread.start()

    def _on_capture_success(self, png_bytes: bytes):
        self._last_capture = png_bytes
        self.capture_group.set_capturing_state(False)
        self.capture_group.show_image(png_bytes)
        self.log_group.log(f"จับภาพสำเร็จ ({len(png_bytes):,} bytes)")

    def _on_capture_error(self, err_msg: str):
        self.capture_group.set_capturing_state(False)
        self.log_group.log(err_msg, tag="error")
        QMessageBox.critical(self, "จับภาพไม่สำเร็จ", err_msg)

    def _handle_save_image(self):
        if not self._last_capture:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Capture Image", "oscilloscope_capture.png", "PNG Files (*.png);;BMP Files (*.bmp);;All Files (*)"
        )
        if not path:
            return

        with open(path, "wb") as f:
            f.write(self._last_capture)

        self.log_group.log(f"บันทึกภาพไปที่: {path}")

    # Channel & Scale Handlers
    def _handle_channel_toggle(self, channel: int, is_on: bool):
        if not self.instrument.is_connected:
            QMessageBox.warning(self, "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อเครื่องมือก่อนตั้งค่า Channel")
            self.channel_group.update_ui_state(channel, not is_on)
            return

        try:
            self.instrument.set_channel_status(channel, is_on)
            self.log_group.log(f"Channel {channel} {'Enable' if is_on else 'Disable'}", tag="cmd")
        except InstrumentError as exc:
            self.log_group.log(str(exc), tag="error")
            self.channel_group.update_ui_state(channel, not is_on)

    def _handle_volt_change(self, channel: int, value: Optional[float], is_channel_switch: bool = False):
        if not self.instrument.is_connected:
            QMessageBox.warning(self, "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อเครื่องมือก่อนตั้งค่า Scale")
            return

        if is_channel_switch:
            try:
                volt_val = self.instrument.get_volt_scale(channel)
                self.scale_group.set_volt_ui(volt_val)
            except InstrumentError as exc:
                self.log_group.log(str(exc), tag="error")
            return

        if value is not None:
            try:
                self.instrument.set_volt_scale(channel, value)
                self.log_group.log(f"ตั้งค่า Channel {channel} Scale ไปที่ {value} V/div", tag="cmd")
            except InstrumentError as exc:
                self.log_group.log(str(exc), tag="error")

    def _handle_time_change(self, value: float):
        if not self.instrument.is_connected:
            QMessageBox.warning(self, "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อเครื่องมือก่อนตั้งค่า Scale")
            return

        try:
            self.instrument.set_time_scale(value)
            self.log_group.log(f"ตั้งค่า Timebase Scale ไปที่ {value} s/div", tag="cmd")
        except InstrumentError as exc:
            self.log_group.log(str(exc), tag="error")

    # Trigger & Measure Handlers
    def _handle_trigger_change(self, source: str, sweep: str, level: float):
        if not self.instrument.is_connected:
            QMessageBox.warning(self, "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อเครื่องมือก่อนตั้งค่า Trigger")
            return
        try:
            self.instrument.set_trigger_source(source)
            self.instrument.set_trigger_sweep(sweep)
            self.instrument.set_trigger_level(level)
            self.log_group.log(f"ตั้งค่า Trigger: Source={source}, Sweep={sweep}, Level={level} V", tag="cmd")
        except InstrumentError as exc:
            self.log_group.log(str(exc), tag="error")

    def _handle_measure(self, item: str, channel: int):
        if not self.instrument.is_connected:
            QMessageBox.warning(self, "ยังไม่ได้เชื่อมต่อ", "กรุณาเชื่อมต่อเครื่องมือก่อนทำการวัดค่า")
            return
        try:
            value = self.instrument.get_measurement(item, channel)
            self.trig_meas_group.show_measure_result(value, item)
            self.log_group.log(f"วัดสัญญาณ ({item}) ที่แชนเนล CH{channel} ได้ค่า: {value}")
        except InstrumentError as exc:
            self.log_group.log(str(exc), tag="error")

    def closeEvent(self, event):
        try:
            if self.instrument.is_connected:
                self.instrument.disconnect()
        except Exception:
            pass
        event.accept()


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OscilloscopeApp()
    window.show()
    sys.exit(app.exec())