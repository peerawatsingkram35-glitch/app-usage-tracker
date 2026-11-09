# App Usage Tracker

A lightweight Windows desktop app that records how long you use each application on your computer. Set a time window to record, watch live usage stats, and export the results to CSV.

> **Note:** The user interface is in Thai.

## Features

- **Scheduled recording:** Set a start and end date/time. The tracker waits until the start time and stops automatically at the end time.
- **Live dashboard:** A table ranks apps by usage time and shows each app's percentage with a bar.
- **Custom refresh rate:** Choose how often the table refreshes, in seconds, minutes, or hours. A countdown shows when the next refresh will happen.
- **Idle detection:** If there's no mouse or keyboard input for a set number of minutes, the tracker pauses counting.
- **Saved data:** Usage is saved to `usage_data.json` every 30 seconds, so your data is kept if you close and reopen the app.
- **CSV export:** Export to a UTF-8 CSV that opens correctly in Excel, including Thai text.
- **No extra libraries:** Uses only the Python standard library. It talks to the Windows API through `ctypes`, so `pywin32` isn't needed.

## Requirements

- Windows 10 or 11
- Python 3.8 or newer, with tkinter (included in the standard Python installer)

## Getting Started

### Run from source

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
python app-usage-tracker.py
```

### Build a standalone .exe

Install PyInstaller:

```bash
pip install pyinstaller
```

Build the app:

```bash
python -m PyInstaller --onefile --noconsole app-usage-tracker.py
```

The executable is saved to `dist/app-usage-tracker.exe`. It runs on computers that don't have Python installed, and it doesn't need administrator rights.

## How to Use

1. **Set the recording window:** Enter the start and end times in `DD/MM/YYYY HH:MM` format, for example `25/12/2025 08:30`. Use the Gregorian year (C.E.), not the Thai Buddhist year. You can also click **ตอนนี้** (Now) and **สิ้นวันนี้** (End of today) to fill them in.
2. **Set the refresh rate:** Enter a number and choose a unit (วินาที = seconds, นาที = minutes, ชั่วโมง = hours).
3. **Set the idle limit:** Enter the number of minutes without input before counting pauses. Enter `0` to always count.
4. **Start recording:** Click **เริ่มบันทึก** (Start recording).
5. **Export:** Click **ส่งออก CSV** (Export CSV) to save the results.

### Buttons

| Button | Action |
|---|---|
| เริ่มบันทึก | Start recording |
| หยุดบันทึก | Stop recording |
| ล้างข้อมูล | Clear all recorded data |
| ส่งออก CSV | Export results to CSV |
| รีเฟรชตอนนี้ | Refresh the table now |

### Status Messages

| Status | Meaning |
|---|---|
| หยุดอยู่ | Stopped |
| รอถึงเวลาเริ่มบันทึก | Waiting for the start time |
| กำลังบันทึก | Recording |
| ไม่ได้ใช้งานเครื่อง | Idle, counting paused |
| ครบเวลาที่กำหนด | Reached the end time, recording finished |

## Data Files

These files are created in the same folder as the program:

| File | Contents |
|---|---|
| `usage_data.json` | Recorded usage time for each app |
| `tracker_config.json` | Your saved settings |

## How It Works

Every second, the tracker:

1. Gets the window in the foreground with `GetForegroundWindow`.
2. Finds the window's process and executable name with `QueryFullProcessImageNameW`.
3. Checks idle time with `GetLastInputInfo`.
4. Adds the elapsed time to that app's total.

Time jumps are capped, so time spent while the computer is asleep isn't counted.

## Limitations

- Windows only.
- Only the active (foreground) window is tracked. Background apps aren't counted.
- Apps are listed by executable name, such as `chrome.exe`. `explorer.exe` covers time spent on the desktop, taskbar, and File Explorer.
- The program must stay open while recording. Minimize it instead of closing it.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.