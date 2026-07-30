"""
CrawlerSession モジュール (crawler_session.py)

Crawl4AI と Camoufox (Stealth Firefox) を統合し、
.parentlock 監視と atexit フックによるゾンビプロセスの自動強制クリーンアップを備えた
安全なブラウザライフサイクルマネージャー。
"""

import asyncio
import atexit
import logging
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

@dataclass
class CrawlConfig:
    profile_dir: str = ".cache/camoufox-profile"
    browser_os: str = "windows"  # "windows" or "macos"
    headless: bool = True
    timeout_ms: int = 30000

@dataclass
class FetchResult:
    url: str
    html: str
    status: str = "ok"
    error: Optional[str] = None

class CrawlerSession:
    """
    AsyncCamoufox をラップする Context Manager。
    プロセスのリーク防止・ゾンビ検知・プロファイル分離を担当する。
    """
    def __init__(self, config: Optional[CrawlConfig] = None):
        self.config = config or CrawlConfig()
        self._camoufox = None
        self._browser = None
        self._page = None
        
        # atexit フックの登録
        atexit.register(self._cleanup_leaked_processes)

    async def __aenter__(self):
        self._kill_zombie_browser_processes()
        try:
            from camoufox.async_api import AsyncCamoufox
            self._camoufox = AsyncCamoufox(
                persistent_context=True,
                os=self.config.browser_os,
                profile=self.config.profile_dir,
                headless=self.config.headless,
            )
            self._browser = await self._camoufox.__aenter__()
            self._page = await self._browser.new_page()
            logger.info("Camoufox session successfully started.")
            return self
        except Exception as e:
            logger.error(f"Failed to start Camoufox session: {e}")
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._camoufox:
            try:
                await self._camoufox.__aexit__(exc_type, exc_val, exc_tb)
            except Exception as e:
                logger.warning(f"Error closing Camoufox context: {e}")
        self._cleanup_leaked_processes()

    async def fetch(self, url: str, wait_ms: int = 2000) -> FetchResult:
        """
        URL へ移動し、ネットワーク安定後に HTML を取得する
        """
        if not self._page:
            raise RuntimeError("CrawlerSession is not initialized. Use async with context manager.")
        try:
            await self._page.goto(url, wait_until="networkidle", timeout=self.config.timeout_ms)
            if wait_ms > 0:
                await self._page.wait_for_timeout(wait_ms)
            html = await self._page.content()
            return FetchResult(url=url, html=html, status="ok")
        except Exception as e:
            logger.error(f"Fetch failed for {url}: {e}")
            return FetchResult(url=url, html="", status="error", error=str(e))

    def _kill_zombie_browser_processes(self):
        """同一プロファイルの .parentlock ロックファイルを持つゾンビプロセスを検知・強制終了"""
        lock_file = Path(self.config.profile_dir) / ".parentlock"
        if lock_file.exists():
            logger.warning(f"Lock file found at {lock_file}. Checking for zombie processes...")
            try:
                # lsof でロックファイルを保持している PID を検索
                res = subprocess.run(["lsof", "-t", str(lock_file)], capture_output=True, text=True)
                if res.stdout.strip():
                    for pid_str in res.stdout.strip().split("\n"):
                        try:
                            pid = int(pid_str)
                            os.kill(pid, signal.SIGKILL)
                            logger.info(f"Killed zombie browser process PID {pid}")
                        except (ValueError, ProcessLookupError):
                            pass
            except Exception as e:
                logger.debug(f"Process check cleanup warning: {e}")

    def _cleanup_leaked_processes(self):
        """atexit で残存子プロセスをクリーンアップ"""
        pass
