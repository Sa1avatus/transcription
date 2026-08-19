import gc
import re
import os
import uuid
import base64
import traceback
from typing import List

import httpx
from config import TMP_DIR, logger, settings


# =============================================================================
# ПОЛУЧЕНИЕ АУДИО ИЗ OMNINET
# =============================================================================

async def fetch_omninet_audio(uid: str) -> List[str]:
    """Запрашивает объект из OMNINET, извлекает Base64-аудио и сохраняет во временные файлы."""
    soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <InvokeScript xmlns="http://www.omninet.de/OtWebSvc/v1">
      <Script name="Transcription.Combo Scripts">
        <Parameters>
          <LongIntVal name="UID">{uid}</LongIntVal>
          <StringVal name="script">GetObjectFields</StringVal>
        </Parameters>
      </Script>
    </InvokeScript>
  </soap12:Body>
</soap12:Envelope>"""

    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8; action="http://omninet.de/InvokeScript"'
    }
    if not all((settings.omninet_url, settings.omninet_login, settings.omninet_password)):
        logger.warning("OMNINET integration is not configured")
        return temp_files
    auth = (settings.omninet_login, settings.omninet_password)
    temp_files: List[str] = []

    try:
        logger.info(f"--- [OMNINET FETCH] Запрос аудио UID={uid}")
        async with httpx.AsyncClient(verify=settings.internal_tls_verify, timeout=120.0) as client:
            response = await client.post(
                settings.omninet_url,
                content=soap_body.encode('utf-8'),
                headers=headers,
                auth=auth,
            )

        if response.status_code != 200:
            logger.error(f"--- [OMNINET FETCH ERROR] HTTP {response.status_code}")
            return temp_files

        text = response.text.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')

        raw_matches = re.findall(r'base64,([^"]+)"', text, re.DOTALL)
        if not raw_matches:
            raw_matches = re.findall(r'>([^<]{1000,})<', text, re.DOTALL)

        logger.info(f"--- [OMNINET FETCH] Найдено потенциальных блоков аудио: {len(raw_matches)}")

        for block_index, raw_data in enumerate(raw_matches):
            try:
                b64_clean = re.sub(r'[^A-Za-z0-9+/=]', '', "".join(raw_data.split()))
                missing = len(b64_clean) % 4
                if missing:
                    b64_clean += '=' * (4 - missing)

                file_bytes = base64.b64decode(b64_clean)

                if (
                    file_bytes.startswith(b'RIFF')
                    or file_bytes.startswith(b'ID3')
                    or file_bytes.startswith(b'\xff\xfb')
                ):
                    temporary_path = os.path.join(TMP_DIR, f"omni_{uid}_{block_index}_{uuid.uuid4().hex[:4]}.wav")
                    with open(temporary_path, "wb") as audio_file:
                        audio_file.write(file_bytes)
                    temp_files.append(temporary_path)
                    logger.info(f"--- [OMNINET FETCH] Сохранено аудио блок {block_index + 1}")

                del file_bytes
            except Exception:
                continue

        gc.collect()
        return temp_files

    except Exception:
        logger.error(f"--- [OMNINET FETCH CRITICAL] {traceback.format_exc()}")
        return temp_files


# =============================================================================
# SOAP CALLBACK (запись результата обратно в OMNINET)
# =============================================================================

async def send_soap_callback(uid: str, text: str) -> None:
    """Отправляет результат транскрибации обратно в OMNINET через SOAP."""
    clean_text = text.replace("]]>", "]]&gt;")
    soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                 xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <InvokeScript xmlns="http://www.omninet.de/OtWebSvc/v1">
      <Script name='Transcription.Combo Scripts'>
        <Parameters>
          <LongIntVal name='UID'>{uid}</LongIntVal>
          <StringVal name='Text'><![CDATA[{clean_text}]]></StringVal>
          <StringVal name='script'>UpdateSC</StringVal>
        </Parameters>
      </Script>
    </InvokeScript>
  </soap12:Body>
</soap12:Envelope>"""

    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8; action="http://omninet.de/InvokeScript"'
    }
    if not all((settings.omninet_url, settings.omninet_login, settings.omninet_password)):
        logger.warning("OMNINET integration is not configured; callback skipped")
        return
    auth = (settings.omninet_login, settings.omninet_password)

    async with httpx.AsyncClient(verify=settings.internal_tls_verify) as client:
        try:
            logger.info(f"--- [CALLBACK] Отправка SOAP для UID {uid}")
            response = await client.post(
                settings.omninet_url,
                content=soap_body.encode('utf-8'),
                headers=headers,
                auth=auth,
                timeout=60.0,
            )
            logger.info(f"--- [CALLBACK] Ответ сервера: {response.status_code}")
            if response.status_code != 200:
                logger.warning(f"--- [CALLBACK] Ошибка сервера: {response.text[:500]}")
        except Exception:
            logger.error(f"--- [CALLBACK ERROR] {traceback.format_exc()}")
