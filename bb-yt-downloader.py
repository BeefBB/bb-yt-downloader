from yt_dlp import YoutubeDL
import re
import sys
import platform
from pathlib import Path
from typing import Dict, Tuple
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from mutagen.flac import FLAC
from mutagen.easyid3 import EasyID3
import threading
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QObject, Signal, QEvent
from PySide6.QtGui import QTextCursor, QIcon, QPalette


def get_base_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", ".")).resolve()


def get_ffmpeg_path() -> Tuple[str, str]:  # ffmpeg 放在程式旁邊
    system = platform.system()
    if system == "Windows":
        ffmpeg_name = "ffmpeg.exe"
        ffprobe_name = "ffprobe.exe"
        subdir = "windows"
    elif system == "Linux":
        ffmpeg_name = "ffmpeg"
        ffprobe_name = "ffprobe"
        subdir = "linux"
    else:
        raise RuntimeError(f"Unsupported OS: {system}")
    
    ffmpeg_path = get_base_dir() / "ffmpeg" / subdir / ffmpeg_name
    ffprobe_path = get_base_dir() / "ffmpeg" / subdir / ffprobe_name

    return str(ffmpeg_path), str(ffprobe_path)


def get_deno_path() -> str:  # deno 放在程式旁邊
    system = platform.system()
    if system == "Windows":
        deno_name = "deno.exe"
        subdir = "windows"
    elif system == "Linux":
        deno_name = "deno"
        subdir = "linux"
    else:
        raise RuntimeError(f"Unsupported OS: {system}")
    
    deno_path = get_base_dir() / "deno" / subdir / deno_name

    return str(deno_path)


def time_display(t: float) -> str:
    t = round(t)
    if t > 100:
        return f"{t // 60}m{t % 60}s"
    return f"{t}s"


def short_path(path: Path, l=3) -> str:
    return str(Path(*path.parts[-l:]))


class Logger:
    def __init__(self, app=None):
        self.app = app


    def _maybe_log(self, prefix, msg, tag: str="normal"):
        if self.app:
            self.app.log_signal.emit(f"{prefix} {msg.strip()}", tag)


    def debug(self, msg):
        self._maybe_log("[yt-dlp][DEBUG]", msg, "normal")


    def info(self, msg):
        self._maybe_log("[yt-dlp][INFO]", msg, "normal")


    def warning(self, msg):
        self._maybe_log("[yt-dlp][WARN]", msg, "warning")


    def error(self, msg):
        self._maybe_log("[yt-dlp][ERROR]", msg, "error")


@lru_cache(maxsize=256)
def get_url_info(url: str) -> Tuple[str, Dict]:
    """
    Get URL information with caching to avoid duplicate yt-dlp calls.
    Returns (content_type, info_dict) for efficient reuse.

    Returns:
        Tuple[str, Dict]: (content_type, info_dict) where content_type is 'video', 'playlist', or 'channel'
    """
    try:
        # Use yt-dlp to extract info without downloading
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,  # Only extract basic info, faster
            'no_warnings': True,
            'skip_download': True,
            'playlist_items': '1',  # Only check first item for speed

            # 'logger': Logger,
            # 'progress_hooks': [ytdl_progress_hook],
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Check if info extraction was successful
            if info is None:
                # Fallback to URL parsing if yt-dlp fails
                parsed_url = urlparse(url)
                query_params = parse_qs(parsed_url.query)

                # Check for channel patterns
                if '/@' in url or '/channel/' in url or '/c/' in url or '/user/' in url:
                    return 'channel', {}
                elif 'list' in query_params:
                    return 'playlist', {}
                else:
                    return 'video', {}

            # Determine content type based on yt-dlp info
            content_type = info.get('_type', 'video')

            # Handle channel detection
            if content_type == 'playlist':
                # Check if it's actually a channel (uploader_id indicates channel content)
                if info.get('uploader_id') and ('/@' in url or '/channel/' in url or '/c/' in url or '/user/' in url):
                    return 'channel', info
                else:
                    return 'playlist', info

            return content_type, info

    except Exception as e:
        print(f"[get_url_info] Exception: {e}")
        # Simple fallback: check URL patterns
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)

        if '/@' in url or '/channel/' in url or '/c/' in url or '/user/' in url:
            return 'channel', {}
        elif 'list' in query_params:
            return 'playlist', {}
        else:
            return 'video', {}


def parse_multiple_urls(input_string: str) -> tuple[list[str], list[str]]:
    # return valid_urls, invalid_urls
    # 把輸入的一坨url變成url陣列
    urls = input_string.replace("https", " https").strip()
    urls = re.split(r'[,\s]+', urls)
    urls = [url.strip() for url in urls if url.strip()]
    urls = list(dict.fromkeys(urls))

    # Validate URLs (basic YouTube URL check)
    valid_urls = []
    invalid_urls = []
    for url in urls:
        if ('youtube.com' in url or 'youtu.be' in url) and (
            '/watch?' in url or
            '/playlist?' in url or
            '/@' in url or
            '/channel/' in url or
            '/c/' in url or
            '/user/' in url or
            'youtu.be/' in url
        ):
            valid_urls.append(url)
        else:
            invalid_urls.append(url)
    return valid_urls, invalid_urls


def download_single_video(url: str, output_path: Path, thread_id: int, format: str,
                          add_subtitles: bool, app=None) -> dict:
    """
    Download a single YouTube video, playlist, or channel.

    Args:
        thread_id (int): Thread identifier for logging

    Returns:
        dict: Result status with success/failure info
    """
    def message_to_ui(message: str="", tag: str=""):
        if app:
            app.log_signal.emit(message, tag)


    def message_progress_to_ui(message: str=""):
        if app:
            app.log_progress_signal.emit(message)


    def add_to_ui(type: str="", amount: int=0):
        if app:
            app.yt_add_signal.emit(type, amount)


    def ytdl_progress_hook(d: dict):
        status = d.get("status", "")
        filename = d.get("filename", "")
        total_MB = float(d.get("total_bytes", 0)) / 1048576
        percent = d.get("_percent_str", "").strip()

        if status == "downloading":
            msg = f"{percent} | {total_MB:5.1f}MB | {Path(filename).name}"
            message_progress_to_ui(msg)

        elif status == "finished":
            message_progress_to_ui(f"✅ {Path(filename).name} - 下載完成, 正在處理..")
            add_to_ui("done", 1)

        elif status == "error":
            message_progress_to_ui(f"❌ {Path(filename).name} - 下載錯誤")
            add_to_ui("done", 1)
    

    if format == "mp4":
        # Configure for video downloads
        format_selector = (
            # Try best video+audio combination first
            'bestvideo[height<=1080]+bestaudio/best[height<=1080]/'
            # Fallback to best available quality
            'best'
        )
        file_extension = 'mp4'
        postprocessors = [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
    elif format == "flac":
        # Configure for audio-only flac downloads
        format_selector = 'bestaudio/best'
        file_extension = 'flac'
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'flac',
        },
        {
            'key': 'EmbedThumbnail',   # 把縮圖嵌進 flac
        },
        {
            'key': 'FFmpegMetadata',
            'add_metadata': True,
        }]
    elif format == "mp3":
        # Configure for audio-only mp3 downloads
        format_selector = 'bestaudio/best'
        file_extension = 'mp3'
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '0',
        },
        {
            'key': 'EmbedThumbnail',   # 把縮圖嵌進 mp3
        },
        {
            'key': 'FFmpegMetadata',
            'add_metadata': True,
        }]
    elif format == "subtitles_only":
        # 只抓字幕, 用srt
        format_selector = 'bestaudio/best'  # format 不重要, 因為會 skip_download
        file_extension = 'srt'
        postprocessors = []

    # Configure yt-dlp options
    ydl_opts = {
        'js_runtimes': {'deno': {'path': get_deno_path()},},

        'format': format_selector,
        'ignoreerrors': True,
        'no_warnings': False,
        'extract_flat': False,
        'postprocessors': postprocessors,
        # Clean up options
        'keepvideo': False,
        'clean_infojson': True,
        'concurrent_fragment_downloads': 2,
        'retries': 3,
        'fragment_retries': 3,

        'ffmpeg_location': get_ffmpeg_path()[0],

        'noplaylist': False,  # Allow playlist downloads

        'download_archive': str(output_path / "downloaded.txt"),
        'nooverwrites': True,

        'writethumbnail': True,

        'subtitleslangs': ['en', 'ja', 'ko', 'zh', 'zh-CN', 'zh-Hans', 'zh-TW', 'zh-Hant', 'zh-HK'],
        # 'subtitleslangs': ['en'],
        'subtitlesformat': 'srt',
        'writeautomaticsub': False,  # 自動字幕

        'logger': Logger(app=app),
        'progress_hooks': [ytdl_progress_hook],
    }

    if format == "mp3":
        ydl_opts['postprocessor_args'] = ['-id3v2_version', '3']

    if add_subtitles:
        ydl_opts['writesubtitles'] = True    # 手動字幕
    else:
        ydl_opts['writesubtitles'] = False

    if format == "subtitles_only":
        ydl_opts['writesubtitles'] = True
        ydl_opts['skip_download'] = True

    # Add merge format for video downloads only
    if format == "mp4":
        ydl_opts['merge_output_format'] = 'mp4'

    # Set different output templates for playlists, channels and single videos
    content_type = get_url_info(url)[0]

    # Debug: Print detection result
    if thread_id == 1:  # Only print for first thread to avoid spam
        message_to_ui(f"🔍 [BBDebug] URL種類: {content_type.title()}", "normal")

    if content_type == 'playlist':
        ydl_opts['outtmpl'] = str(output_path / "%(playlist_title)s" / f"%(playlist_index)s-%(title)s.{file_extension}")
        message_to_ui(f"📋 [人員 {thread_id}] 偵測到清單URL", "normal")
    elif content_type == 'channel':
        ydl_opts['outtmpl'] = str(output_path / "%(uploader)s" / f"%(upload_date)s-%(title)s.{file_extension}")
        message_to_ui(f"📺 [人員 {thread_id}] 偵測到頻道URL", "normal")
    else:  # single video
        ydl_opts['outtmpl'] = str(output_path / f"%(title)s.{file_extension}")
        message_to_ui(f"🎥 [人員 {thread_id}] 偵測到單一影片URL", "normal")

    try:
        with YoutubeDL(ydl_opts) as ydl:
            # Extract fresh info for download (cached info is only for detection)
            info = ydl.extract_info(url, download=False)

            # Check if info extraction was successful
            if info is None:
                return {
                    'url': url,
                    'success': False,
                    'message': f"❌ [人員 {thread_id}] 沒有辦法解析, 可能是私人的或是被YT阻擋了"
                }

            if info.get('_type') == 'playlist':
                title = info.get('title', 'Unknown Playlist')
                video_count = len(info.get('entries', []))

                message_to_ui(f"📋 [人員 {thread_id}] {content_type.title()}: '{title}' ({video_count} videos)", "normal")
                add_to_ui("add", video_count)

                # Ensure we have entries to download
                if video_count == 0:
                    return {
                        'url': url,
                        'success': False,
                        'message': f"❌ [人員 {thread_id}] {content_type.title()} 看起來像空的或是私人的或是全部都已經下載過了"
                    }
            elif info.get('_type') == 'video':
                add_to_ui("add", 1)

            # Download content
            ydl.download([url])

            if info.get('_type') == 'playlist':
                title = info.get('title', f'Unknown {content_type.title()}')
                video_count = len(info.get('entries', []))
                return {
                    'url': url,
                    'success': True,
                    'message': f"✅ [人員 {thread_id}] {content_type.title()} '{title}' 下載完成! ({video_count} {format})"
                }
            else:
                return {
                    'url': url,
                    'success': True,
                    'message': f"✅ [人員 {thread_id}] {format} 下載完成!"
                }

    except Exception as e:
        return {
            'url': url,
            'success': False,
            'message': f"❌ [人員 {thread_id}] Error: {str(e)}"
        }


def sort_txt_file(path: Path):
    try:
        file = path / "downloaded.txt"
        if not file.exists():
            return {
                "success": False,
                "message": "downloaded.txt 不存在",
            }
        
        with open(file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        lines = [line.strip() for line in lines if line.strip()]
        lines.sort()

        with open(file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        return {
            "success": True,
            "message": "",
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"downloaded.txt排序失敗 > {e}",
        }


def remove_prefix(folder_path: Path):
    if folder_path == Path(""):
        return {
            "success": False,
            "message": "❌ 請先選擇輸出資料夾",
        }

    files = [p for p in folder_path.iterdir() if p.suffix.lower() in {".flac", ".mp3"}]
    total = len(files)

    skip_sum = 0
    for p in files:
        # 去掉「前綴數字 + -」
        new_name = re.sub(r'^\d+-', '', p.name)
        new_path = p.with_name(new_name)

        if not new_path.exists():
            try:
                p.rename(new_path)
            except Exception:
                skip_sum += 1
        else:
            skip_sum += 1

    return {
        "success": True,
        "message": f"已去除前方數字前綴，共檢查 {total} 個檔案 (跳過: {skip_sum})",
    }


def remove_flacflac(folder_path: Path):
    if folder_path == Path(""):
        return {
            "success": False,
            "message": "❌ 請先選擇輸出資料夾",
        }
    
    total = 0
    skip_sum = 0

    for p in folder_path.rglob("*"):
        if not p.suffix.lower() in {".flac", ".mp3"}:
                continue
        
        total += 1
        new_name = p.name

        if p.name.lower().endswith(".flac.flac"):
            new_name = p.name[:-5]
        elif p.name.lower().endswith(".mp3.mp3"):
            new_name = p.name[:-4]
        else:
            skip_sum += 1
            continue

        try:
            p.rename(p.with_name(new_name))
        except Exception:
            skip_sum += 1
            continue

    return {
        "success": True,
        "message": f"已去除多餘的副檔名，共檢查 {total} 個檔案 (跳過: {skip_sum})",
    }


def sort_names(folder_path: Path):
    message = []

    if folder_path == Path(""):
        return {
            "success": False,
            "message": "❌ 請先選擇輸出資料夾",
        }
    
    # 排序:演出者 > 標題 > 檔名
    def get_sort_key(file):
        try:
            tags = FLAC(file) if file.suffix.lower() == ".flac" else EasyID3(file)
            
            artist = tags.get("artist", ["Unknown"])[0] or "Unknown"
            title = tags.get("title", [file.stem])[0] or file.stem
            return (artist.lower(), title.lower())
        except Exception:
            return ("Unknown", file.stem.lower())

    files = [p for p in folder_path.iterdir() if p.suffix.lower() in {".flac", ".mp3"}]
    total = len(files)
    message.append(f"找到 {total} 個 flac / mp3")

    width = len(str(total))

    files.sort(key=get_sort_key)

    skip_sum = 0
    for i, filename in enumerate(files, 1):
        number = str(i).zfill(width)  # 自動補0, 例如 "001"
        
        old_path = filename
        new_name = f"{number}-{filename.name}"
        new_path = folder_path / new_name

        try:
            old_path.rename(new_path)
        except Exception as e:
            skip_sum += 1

    message.append(f"已重新編號所有檔案 (跳過: {skip_sum})")
    
    return {
        "success": True,
        "message": message,
    }


class App(QObject):
    log_signal = Signal(str, str)
    log_progress_signal = Signal(str)
    title_signal = Signal(str)
    button_signal = Signal(bool)
    yt_add_signal = Signal(str, int)

    def __init__(self):
        super().__init__()

        loader = QUiLoader()
        file = QFile(get_base_dir() / "bb-yt-downloader.ui")
        file.open(QFile.ReadOnly)  # type: ignore
        self.ui = loader.load(file)
        file.close()

        self._connect()
        self._init_vars()
        self.add_icon()

        self._enable_drag_drop()


    def _connect(self):
        self.ui.btn_output.clicked.connect(self.browse_output_folder)  # type: ignore

        self.ui.btn_start_download.clicked.connect(self.start_download)  # type: ignore
        self.ui.btn_remove_prefix.clicked.connect(self.to_remove_prefix)  # type: ignore
        self.ui.btn_remove_flacflac.clicked.connect(self.to_remove_flacflac)  # type: ignore
        self.ui.btn_sort_names.clicked.connect(self.to_sort_names)  # type: ignore

        self.ui.pushButton_showinfo.clicked.connect(self.showinfo)  # type: ignore

        self.log_signal.connect(self._log_message_ui)
        self.log_progress_signal.connect(self._log_message_progress_ui)
        self.title_signal.connect(self._update_title_ui)
        self.button_signal.connect(self._set_buttons_state_ui)
        self.yt_add_signal.connect(self._update_yt_add_ui)


    def _init_vars(self):
        self.downloaded_count = 0
        self.total_count = 0
        self.update_title_msg = ""

        self.output_folder_path = Path("")

        self.ui.label_output.setText("選擇或拖曳資料夾到這裡")  # type: ignore

        self.ui.comboBox_format.setCurrentText("MP3 音樂")  # type: ignore
        self.ui.checkBox_subtitle.setChecked(False)  # type: ignore
        self.ui.spinBox_max_workers.setValue(5)  # type: ignore
        self.ui.checkBox_debug.setChecked(False)  # type: ignore

        self.ui.text_log.setText("懶人包: \"選擇輸出資料夾\" > \"貼上網址\" > \"下載 !\"\n")  # type: ignore

        self.ui.label_version.setText("3.2.0")  # type: ignore


    def filter_message(self, msg: str) -> str:
        ok = not msg.startswith("[yt-dlp]")
        msg = msg.strip()

        if "Extracting URL" in msg:
            ok = True
            msg = msg.replace("Extracting URL", "分析URL")

        if "[download] Finished downloading playlist" in msg:
            ok = True
            msg = msg.replace("[download] Finished downloading playlist", "清單下載完成")

        if "[Metadata] Adding metadata to" in msg:
            ok = True
            msg = msg.replace("[Metadata] Adding metadata to", "添加元數據 ")

        msg = re.sub(r'^(?:\[(?:yt-dlp|DEBUG|INFO|WARN|ERROR|youtube|youtube:tab|info)\]\s*)+', '', msg)

        return msg.strip() if ok else ""


    def _log_message_ui(self, msg: str, tag: str = ""):
        if not self.ui.checkBox_debug.isChecked():  # type: ignore
            msg = self.filter_message(msg)
            if not msg:
                return

        if tag == "warning":
            color = "orange"
        elif tag == "error":
            color = "red"
        else:
            palette = self.ui.text_log.palette()  # type: ignore
            color = palette.color(QPalette.Text).name()  # type: ignore

        self.ui.text_log.append(f'<span style="color: {color};">{msg}</span>')  # type: ignore
        self.ui.text_log.moveCursor(QTextCursor.End)  # type: ignore


    def _log_message_progress_ui(self, msg: str):
        self.ui.label_progress.setText(msg)  # type: ignore


    def _update_title_ui(self, msg: str = ""):
        if msg == "":
            msg = self.update_title_msg
        else:
            self.update_title_msg = msg


        title = f"BB YT 下載器 [{self.downloaded_count} / {self.total_count}] " + msg
        self.ui.setWindowTitle(title)


    def _set_buttons_state_ui(self, state):  # type: ignore
        self.ui.btn_output.setEnabled(state)  # type: ignore

        self.ui.lineEdit_url.setEnabled(state)  # type: ignore

        self.ui.comboBox_format.setEnabled(state)  # type: ignore
        self.ui.checkBox_subtitle.setEnabled(state)  # type: ignore
        self.ui.spinBox_max_workers.setEnabled(state)  # type: ignore

        self.ui.btn_start_download.setEnabled(state)  # type: ignore
        self.ui.btn_remove_prefix.setEnabled(state)  # type: ignore
        self.ui.btn_remove_flacflac.setEnabled(state)  # type: ignore
        self.ui.btn_sort_names.setEnabled(state)  # type: ignore


    def _update_yt_add_ui(self, type: str="", amount: int=0):
        if type == "add":
            self.total_count += amount
        elif type == "done":
            self.downloaded_count += amount

        self._update_title_ui()


    def add_icon(self):
        system = platform.system()
        if system == "Windows":
            icon="bb-yt-downloader.ico"
        elif system == "Linux":
            icon="bb-yt-downloader.png"
        else:
            return
        
        try:
            self.ui.setWindowIcon(QIcon(str(get_base_dir() / "icon" / icon)))
        except Exception:
            pass


    def browse_output_folder(self):
        selected = QFileDialog.getExistingDirectory(self.ui, "選擇資料夾")
        if selected:
            self.output_folder_path = Path(selected)
            self.ui.label_output.setText(short_path(Path(selected)))  # type: ignore


    def _enable_drag_drop(self):
        for widget in [self.ui.label_output]:  # type: ignore
            widget.setAcceptDrops(True)
            widget.installEventFilter(self)


    def eventFilter(self, obj, event):
        if event.type() == QEvent.DragEnter:  # type: ignore
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
                return True

        elif event.type() == QEvent.Drop:  # type: ignore
            urls = event.mimeData().urls()
            if not urls:
                return True

            path = Path(urls[0].toLocalFile())

            if not path.exists():
                return True

            if obj == self.ui.label_output:  # type: ignore
                if path.is_dir():
                    self.output_folder_path = path
                    self.ui.label_output.setText(short_path(path))  # type: ignore

            return True

        return super().eventFilter(obj, event)


    def start_download(self):
        output_folder_path = self.output_folder_path
        if output_folder_path == Path(""):
            self.log_signal.emit("❌ 請先選擇輸出資料夾", "warning")
            return
        output_folder_path.mkdir(exist_ok=True)

        url = self.ui.lineEdit_url.text()  # type: ignore
        if not url:
            self.log_signal.emit("❌ 請先輸入 YouTube URL", "warning")
            return
        
        urls, invalid_urls = parse_multiple_urls(url)
        for url in invalid_urls:
            self.log_signal.emit(f"⚠️  跳過的 URL: {url}", "warning")
        if not urls:
            self.log_signal.emit(f"⚠️  沒有有效的 YouTube URL", "warning")
            return

        format_text = self.ui.comboBox_format.currentText()  # type: ignore
        subtitle = self.ui.checkBox_subtitle.isChecked()  # type: ignore
        max_workers = self.ui.spinBox_max_workers.value()  # type: ignore


        def _worker():
            try:
                self.log_signal.emit("-" * 44, "normal")
                self.log_signal.emit(f"🚀 開始下載 {len(urls)} 個URL(s), 同時使用 {max_workers} 個序列...", "normal")
                self.log_signal.emit(f"📁 目的地: {output_folder_path}", "normal")
                self.log_signal.emit(f"🎧 格式: {format_text}", "normal")
                self.title_signal.emit("🚀 開始下載...")
                self.button_signal.emit(False)

                self.downloaded_count = self.total_count = 0

                if "MP4" in format_text:
                    format = "mp4"
                elif "FLAC" in format_text:
                    format = "flac"
                elif "MP3" in format_text:
                    format = "mp3"
                else:
                    format = "subtitles_only"

                playlist_count = sum(get_url_info(url)[0] == 'playlist' for url in urls)
                channel_count = sum(get_url_info(url)[0] == 'channel' for url in urls)
                video_count = len(urls) - playlist_count - channel_count

                content_summary = []
                if playlist_count > 0:
                    content_summary.append(f"{playlist_count} 清單")
                if channel_count > 0:
                    content_summary.append(f"{channel_count} 頻道")
                if video_count > 0:
                    content_summary.append(f"{video_count} 影片")

                if content_summary:
                    self.log_signal.emit(f"📋 內容: {' + '.join(content_summary)}", "normal")
                else:
                    self.log_signal.emit(f"📋 內容: 未知類型", "normal")

                self.log_signal.emit("-" * 44, "normal")

                success_amount = 0

                with ThreadPoolExecutor(max_workers=max_workers) as executor: # type: ignore
                    future_to_idx = {
                        executor.submit(download_single_video, url=url, output_path=output_folder_path, thread_id=i + 1,
                                        format=format, add_subtitles=subtitle, app=self): url
                        for i, url in enumerate(urls)
                    }

                    for future in as_completed(future_to_idx):
                        result = future.result()
                        result_path = result['url']
                        result_success = result['success']
                        result_message = result['message']

                        self.title_signal.emit("")

                        if not result_success:
                            self.log_signal.emit("❌ 處理發生例外", "error")
                            self.log_signal.emit(f" > {result_path}", "normal")
                            self.log_signal.emit(f" > {result_message}", "normal")
                            continue
                        success_amount += 1

                self.log_signal.emit("-" * 44, "normal")
                self.log_signal.emit(f"成功 {success_amount} / 失敗 {len(urls) - success_amount} / 總共 {len(urls)} 個URL", "normal")

                if success_amount:
                    self.log_signal.emit(f"🎉 所有檔案儲存在:", "normal")
                    self.log_signal.emit(f"{output_folder_path}", "normal")

                self.log_signal.emit("✅ 下載結束", "normal")
                self.title_signal.emit("✅ 下載結束")

                result_sort = sort_txt_file(output_folder_path)
                if not result_sort["success"]:
                    self.log_signal.emit(result_sort["message"], "error")

            except Exception as e:
                self.log_signal.emit("❌ 處理發生例外", "error")
                self.log_signal.emit(f": {e}", "error")
                self.title_signal.emit(f"❌ 處理發生例外")
            finally:
                self.button_signal.emit(True)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()


    def to_remove_prefix(self):
        result =  remove_prefix(self.output_folder_path)
        if result["success"]:
            self.log_signal.emit(result["message"], "normal")
        else:
            self.log_signal.emit(result["message"], "warning")


    def to_remove_flacflac(self):
        result =  remove_flacflac(self.output_folder_path)
        if result["success"]:
            self.log_signal.emit(result["message"], "normal")
        else:
            self.log_signal.emit(result["message"], "warning")


    def to_sort_names(self):
        result =  sort_names(self.output_folder_path)
        if result["success"]:
            self.log_signal.emit(result["message"], "normal")
        else:
            self.log_signal.emit(result["message"], "warning")


    def showinfo(self):
        QMessageBox.information(
            self.ui,
            "BB Helper",
            "1. 按\"選擇輸出資料夾\"\n"
            "2. 在 Youtube 網址 貼上網址\n"
            "3. 按\"下載 !\"\n"
            "\n"
            "可以一次很多個網址, 用逗號或空格隔開都可以, 像是:\n"
            "url1,url2\n"
            "url1 url2\n"
            "url1, url2 url3,url4\n"
            "(或是不隔開也可以)\n"
            "\n"
            "網址可以是:\n"
            "單影片(https://www.youtube.com/watch?v=...)\n"
            "播放清單(https://www.youtube.com/playlist?list=...)\n"
            "頻道(https://www.youtube.com/@channelname)\n"
            "頻道(https://www.youtube.com/channel/UC...)\n"
            "頻道(https://www.youtube.com/c/channelname)\n"
            "頻道(https://www.youtube.com/user/username)\n"
            "\n"
            "也可以單影片和播放清單一起下載, 都丟在一起吧\n"
            "\n"
            "如果是你自己私人的, 記得設定成不公開(知道連結才能存取)\n"
            "\n"
            "有些時候會下載失敗, 像是清單裡會少幾首歌(YT在搞)\n"
            "重新下載一次就好, 不會重新下載已經下載過的\n"
            "下載過的會記錄在\"downloaded.txt\"(在你選的資料夾裡)\n"
            "如果要重新下載下載過的, 就把\"downloaded.txt\"刪了\n"
            "(或是把裡面的某幾行刪了(如果你知道你下載了什麼))\n"
            "(裡面是每個影片專屬的代碼, 可以從網址的v=...找到)\n"
            "\n"
            "字幕要YT上面有才抓的到, 所以看起來有缺是正常的\n"
            "也有可能抓到YT不知道藏在哪裡的破爛字幕(YT又在搞)\n"
            "\n"
            "同時下載數量是指多個網址一起下載, 不是指單個播放清單裡的影片一起下載\n"
            "(也就是說不用管它)\n"
            "\n"
            "\"下載 !\"右邊的三個按鈕是一些小工具, 你看到下載後的東西應該就會懂\n"
            "(雖然不嚴重, 但還是說一下它們是不可逆的:))\n"
            "這幾個工具只針對目前的資料夾(除了 \"去除多餘的副檔名\"), 如果你是下載播放清單, 播放清單會在自己的資料夾裡, 要你自己再選一次 \"選擇輸出資料夾\"\n"
            )


    def run(self):
        self.ui.show()


if __name__ == "__main__":
    qt_app = QApplication(sys.argv)
    app = App()
    app.run()
    sys.exit(qt_app.exec())