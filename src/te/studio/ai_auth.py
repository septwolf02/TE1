"""Separate Windows-protected API and ChatGPT credentials."""
from __future__ import annotations

import base64
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import secrets
from threading import Lock
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid
import webbrowser


AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
SERVICE = "TE Studio AI"


class CredentialStore:
    """Use Windows Credential Manager; never fall back to plaintext storage."""

    def _keyring(self):
        if os.name != "nt":
            raise RuntimeError("Lưu AI credentials chỉ hỗ trợ Windows Credential Manager.")
        import keyring
        from keyring.backends.Windows import WinVaultKeyring
        backend = keyring.get_keyring()
        if not isinstance(backend, WinVaultKeyring):
            raise RuntimeError("Windows Credential Manager chưa sẵn sàng.")
        return keyring

    def get(self, name: str) -> str | None:
        store = self._keyring()
        manifest = store.get_password(SERVICE, name)
        if manifest is None:
            return None
        data = json.loads(manifest)
        chunks = [store.get_password(SERVICE, f"{name}:{data['generation']}:{index}")
                  for index in range(data["count"])]
        if any(chunk is None for chunk in chunks):
            raise RuntimeError("AI credentials trong Windows Credential Manager chưa đầy đủ.")
        return "".join(chunks)

    def set(self, name: str, value: str) -> None:
        store = self._keyring()
        old = store.get_password(SERVICE, name)
        generation = uuid.uuid4().hex
        chunks = [value[index:index + 1000] for index in range(0, len(value), 1000)]
        for index, chunk in enumerate(chunks):
            store.set_password(SERVICE, f"{name}:{generation}:{index}", chunk)
        store.set_password(SERVICE, name, json.dumps({"generation": generation, "count": len(chunks)}))
        if old:
            self._delete_chunks(store, name, json.loads(old))

    @staticmethod
    def _delete_chunks(store, name: str, manifest: dict) -> None:
        for index in range(manifest["count"]):
            try:
                store.delete_password(SERVICE, f"{name}:{manifest['generation']}:{index}")
            except store.errors.PasswordDeleteError:
                pass

    def delete(self, name: str) -> None:
        store = self._keyring()
        manifest = store.get_password(SERVICE, name)
        if manifest:
            self._delete_chunks(store, name, json.loads(manifest))
        try:
            store.delete_password(SERVICE, name)
        except store.errors.PasswordDeleteError:
            pass


def _post_form(url: str, fields: dict) -> dict:
    request = Request(url, data=urlencode(fields).encode(),
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


class ChatGPTAuth:
    def __init__(self, credentials: CredentialStore, host_id: str):
        self.credentials = credentials
        self.host_id = host_id
        self._refresh_lock = Lock()

    @staticmethod
    def new_host_id() -> str:
        return "urn:uuid:" + str(uuid.uuid4())

    def record(self) -> dict | None:
        value = self.credentials.get("chatgpt")
        return json.loads(value) if value else None

    def _save(self, record: dict) -> None:
        self.credentials.set("chatgpt", json.dumps(record, ensure_ascii=False))

    @staticmethod
    def _verify_id_token(token: str, client_id: str, nonce: str) -> dict:
        import jwt
        jwks = jwt.PyJWKClient(AUTH + "/.well-known/jwks.json")
        key = jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id,
                            issuer=AUTH, options={"require": ["exp", "iss", "aud", "sub"]})
        if claims.get("nonce") != nonce or not isinstance(claims.get("sub"), str):
            raise ValueError("ID token không khớp nonce hoặc account.")
        return claims

    def connect(self, *, consent: bool = False) -> dict:
        previous = self.record()
        client_id = previous["client_id"] if previous else "dynamic_agent_client"
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        callback: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if urlsplit(self.path).path != "/auth/callback":
                    self.send_error(404)
                    return
                callback.update({key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<h1>TE Studio</h1><p>Có thể trở lại ứng dụng.</p>".encode())

            def log_message(self, *_args):
                pass  # Callback query may contain authorization codes.

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        params = {"client_id": client_id, "ext_agent_host_id": self.host_id,
                  "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
                  "resource": RESOURCE, "state": state, "nonce": nonce,
                  "code_challenge_method": "S256", "code_challenge": challenge}
        if previous:
            if previous.get("id_token"):
                params["id_token_hint"] = previous["id_token"]
            if previous.get("email"):
                params["login_hint"] = previous["email"]
        else:
            params["agent_name_hint"] = "TE Studio"
        if consent:
            params["prompt"] = "consent"
        try:
            webbrowser.open(AUTH + "/api/accounts/authorize?" + urlencode(params))
            deadline = time.monotonic() + 180
            while not callback and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if not callback:
            raise TimeoutError("Không nhận được callback ChatGPT trong 3 phút.")
        if not secrets.compare_digest(callback.get("state", ""), state):
            raise ValueError("OAuth state không khớp.")
        if callback.get("error"):
            raise PermissionError("ChatGPT không cấp quyền đăng nhập hoặc sử dụng plan.")
        issued = callback.get("client_id", client_id)
        if issued == "dynamic_agent_client" or (previous and issued != client_id):
            raise ValueError("Client registration không hợp lệ.")
        token = _post_form(AUTH + "/api/accounts/oauth/token", {
            "grant_type": "authorization_code", "client_id": issued,
            "code": callback["code"], "code_verifier": verifier,
            "redirect_uri": redirect, "resource": RESOURCE})
        if token.get("token_type", "").lower() != "bearer" or not token.get("access_token"):
            raise ValueError("OAuth token response không hợp lệ.")
        claims = self._verify_id_token(token["id_token"], issued, nonce)
        if previous and claims["sub"] != previous["subject"]:
            raise ValueError("ChatGPT account khác account đã lưu.")
        scopes = token.get("scope", "").split()
        record = {"client_id": issued, "subject": claims["sub"],
                  "email": claims.get("email", ""), "host_id": self.host_id,
                  "access_token": token["access_token"], "refresh_token": token.get("refresh_token"),
                  "id_token": token["id_token"], "scopes": scopes,
                  "expires_at": time.time() + int(token["expires_in"])}
        self._save(record)
        return record

    def access_token(self) -> str:
        with self._refresh_lock:
            record = self.record()
            if not record:
                raise PermissionError("Chưa kết nối ChatGPT.")
            if "chatgpt.tokens.use.direct" not in record["scopes"] or "resource.invoke" not in record["scopes"]:
                raise PermissionError("ChatGPT đã đăng nhập nhưng chưa cấp quyền dùng plan. Hãy kết nối lại.")
            if time.time() >= record["expires_at"] - 120:
                if not record.get("refresh_token"):
                    raise PermissionError("ChatGPT session hết hạn. Hãy kết nối lại.")
                token = _post_form(AUTH + "/api/accounts/oauth/token", {
                    "grant_type": "refresh_token", "client_id": record["client_id"],
                    "refresh_token": record["refresh_token"], "resource": RESOURCE})
                if token.get("token_type", "").lower() != "bearer" or not token.get("access_token"):
                    raise PermissionError("ChatGPT token refresh không hợp lệ.")
                scopes = token.get("scope", " ".join(record["scopes"])).split()
                if "chatgpt.tokens.use.direct" not in scopes:
                    raise PermissionError("ChatGPT plan permission đã bị thu hồi.")
                record.update(access_token=token["access_token"], refresh_token=token["refresh_token"],
                              scopes=scopes, expires_at=time.time() + int(token["expires_in"]))
                self._save(record)
            return record["access_token"]

    def disconnect(self) -> bool:
        record = self.record()
        revoked = True
        if record and record.get("refresh_token"):
            try:
                with urlopen(AUTH + "/.well-known/openid-configuration", timeout=10) as response:
                    endpoint = json.load(response)["revocation_endpoint"]
                request = Request(endpoint, data=urlencode({"token": record["refresh_token"],
                    "token_type_hint": "refresh_token", "client_id": record["client_id"]}).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                with urlopen(request, timeout=20):
                    pass
            except Exception:
                revoked = False
        self.credentials.delete("chatgpt")
        return revoked
