import os
import tempfile
from pathlib import Path

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

PORT = int(os.environ.get("PORT", "8080"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "60"))


# ============================================================
# HELPERS
# ============================================================

def env(name, required=False):
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def ok(data=None, **kwargs):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(kwargs)
    return jsonify(payload), 200


def fail(message, status=400, **kwargs):
    payload = {"ok": False, "error": message}
    payload.update(kwargs)
    return jsonify(payload), status


def post_json(url, headers=None, body=None, timeout=REQUEST_TIMEOUT):
    response = requests.post(
        url,
        headers=headers or {},
        json=body or {},
        timeout=timeout,
    )

    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}

    if not response.ok:
        raise RuntimeError(f"{response.status_code}: {data}")

    return data


# ============================================================
# YOUTUBE
# ============================================================

def youtube_credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    access_token = env("YOUTUBE_ACCESS_TOKEN")
    refresh_token = env("YOUTUBE_REFRESH_TOKEN")
    client_id = env("YOUTUBE_CLIENT_ID")
    client_secret = env("YOUTUBE_CLIENT_SECRET")

    if not access_token and not refresh_token:
        raise RuntimeError(
            "Set YOUTUBE_ACCESS_TOKEN or YOUTUBE_REFRESH_TOKEN."
        )

    credentials = Credentials(
        token=access_token or None,
        refresh_token=refresh_token or None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id or None,
        client_secret=client_secret or None,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    return credentials


def download_video(video_url):
    response = requests.get(
        video_url,
        stream=True,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    response.raise_for_status()

    suffix = ".mp4"

    content_type = response.headers.get(
        "content-type",
        ""
    ).lower()

    if "quicktime" in content_type:
        suffix = ".mov"
    elif "webm" in content_type:
        suffix = ".webm"

    temp = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=suffix
    )

    try:
        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):
            if chunk:
                temp.write(chunk)

        temp.close()
        return temp.name

    except Exception:
        temp.close()
        Path(temp.name).unlink(missing_ok=True)
        raise


def publish_youtube(
    video_url,
    title,
    description="",
    tags=None
):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = youtube_credentials()

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials
    )

    file_path = download_video(video_url)

    try:
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags or [],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": env(
                    "YOUTUBE_PRIVACY",
                    "private"
                ),
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            file_path,
            chunksize=-1,
            resumable=True,
            mimetype="video/*",
        )

        result = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        ).execute()

        video_id = result.get("id")

        return {
            "platform": "youtube",
            "status": "uploaded",
            "video_id": video_id,
            "url": (
                f"https://www.youtube.com/watch?v={video_id}"
                if video_id
                else None
            ),
        }

    finally:
        Path(file_path).unlink(
            missing_ok=True
        )


# ============================================================
# TIKTOK
# ============================================================

TIKTOK_BASE = (
    "https://open.tiktokapis.com/v2"
)


def tiktok_headers():
    token = env(
        "TIKTOK_ACCESS_TOKEN",
        required=True
    )

    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": (
            "application/json; charset=UTF-8"
        ),
    }


def tiktok_creator_info():

    response = requests.post(
        f"{TIKTOK_BASE}/post/publish/"
        "creator_info/query/",
        headers=tiktok_headers(),
        json={},
        timeout=REQUEST_TIMEOUT,
    )

    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}

    if not response.ok:
        raise RuntimeError(
            f"TikTok creator info failed: {data}"
        )

    error = data.get("error", {})

    if error.get("code") not in (
        None,
        "",
        "ok"
    ):
        raise RuntimeError(
            f"TikTok creator info failed: {error}"
        )

    return data.get("data", {})


def publish_tiktok(
    video_url,
    title
):

    creator = tiktok_creator_info()

    allowed_privacy = creator.get(
        "privacy_level_options",
        []
    )

    requested = env(
        "TIKTOK_PRIVACY_LEVEL",
        "PUBLIC_TO_EVERYONE"
    )

    if requested in allowed_privacy:
        privacy = requested

    elif "PUBLIC_TO_EVERYONE" in allowed_privacy:
        privacy = "PUBLIC_TO_EVERYONE"

    elif allowed_privacy:
        privacy = allowed_privacy[0]

    else:
        raise RuntimeError(
            "TikTok returned no privacy level options."
        )

    body = {
        "post_info": {
            "title": title[:2200],
            "privacy_level": privacy,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "is_aigc": True,
        },
        "source_info": {
            "source": "PULL_FROM_URL",
            "video_url": video_url,
        },
    }

    data = post_json(
        f"{TIKTOK_BASE}/post/publish/"
        "video/init/",
        headers=tiktok_headers(),
        body=body,
    )

    error = data.get(
        "error",
        {}
    )

    if error.get("code") not in (
        None,
        "",
        "ok"
    ):
        raise RuntimeError(
            f"TikTok publish failed: {error}"
        )

    publish_id = data.get(
        "data",
        {}
    ).get("publish_id")

    return {
        "platform": "tiktok",
        "status": "submitted",
        "publish_id": publish_id,
        "privacy_level": privacy,
    }


# ============================================================
# INSTAGRAM REELS
# ============================================================

def publish_instagram(
    video_url,
    caption
):

    access_token = env(
        "INSTAGRAM_ACCESS_TOKEN",
        required=True
    )

    ig_user_id = env(
        "INSTAGRAM_USER_ID",
        required=True
    )

    graph_version = env(
        "META_GRAPH_VERSION",
        "v24.0"
    )

    base = (
        f"https://graph.facebook.com/"
        f"{graph_version}"
    )

    create_response = requests.post(
        f"{base}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption[:2200],
            "access_token": access_token,
        },
        timeout=REQUEST_TIMEOUT,
    )

    try:
        create_data = create_response.json()
    except ValueError:
        create_data = {
            "raw": create_response.text
        }

    if not create_response.ok:
        raise RuntimeError(
            "Instagram media container failed: "
            f"{create_data}"
        )

    container_id = create_data.get("id")

    if not container_id:
        raise RuntimeError(
            "Instagram returned no container ID: "
            f"{create_data}"
        )

    publish_response = requests.post(
        f"{base}/{ig_user_id}/media_publish",
        data={
            "creation_id": container_id,
            "access_token": access_token,
        },
        timeout=REQUEST_TIMEOUT,
    )

    try:
        publish_data = publish_response.json()
    except ValueError:
        publish_data = {
            "raw": publish_response.text
        }

    if not publish_response.ok:
        raise RuntimeError(
            "Instagram publish failed: "
            f"{publish_data}"
        )

    return {
        "platform": "instagram",
        "status": "published",
        "container_id": container_id,
        "media_id": publish_data.get("id"),
    }


# ============================================================
# MAIN ROUTES
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "service": (
            "BroWhat Publishing Engine"
        ),
        "status": "online",
        "version": "2.0",
        "platforms": [
            "youtube",
            "tiktok",
            "instagram"
        ],
    })


@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "status": "healthy",
        "service": (
            "BroWhat Publishing Engine"
        ),
    })


@app.route("/config", methods=["GET"])
def config():

    return jsonify({
        "youtube": bool(
            os.environ.get(
                "YOUTUBE_ACCESS_TOKEN"
            )
            or os.environ.get(
                "YOUTUBE_REFRESH_TOKEN"
            )
        ),
        "tiktok": bool(
            os.environ.get(
                "TIKTOK_ACCESS_TOKEN"
            )
        ),
        "instagram": bool(
            os.environ.get(
                "INSTAGRAM_ACCESS_TOKEN"
            )
            and os.environ.get(
                "INSTAGRAM_USER_ID"
            )
        ),
    })


# ============================================================
# PUBLISH ALL
# ============================================================

@app.route("/publish", methods=["POST"])
def publish():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    video_url = str(
        payload.get(
            "video_url",
            ""
        )
    ).strip()

    title = str(
        payload.get(
            "title",
            "BroWhat"
        )
    ).strip()

    description = str(
        payload.get(
            "description",
            ""
        )
    ).strip()

    tags = payload.get(
        "tags"
    ) or []

    platforms = payload.get(
        "platforms",
        [
            "youtube",
            "tiktok",
            "instagram"
        ]
    )

    if not video_url:
        return fail(
            "video_url is required."
        )

    if (
        not isinstance(
            platforms,
            list
        )
        or not platforms
    ):
        return fail(
            "platforms must be a "
            "non-empty list."
        )

    results = {}
    errors = {}

    for platform in platforms:

        platform = str(
            platform
        ).lower().strip()

        try:

            if platform == "youtube":

                results["youtube"] = (
                    publish_youtube(
                        video_url,
                        title,
                        description,
                        tags,
                    )
                )

            elif platform == "tiktok":

                results["tiktok"] = (
                    publish_tiktok(
                        video_url,
                        title,
                    )
                )

            elif platform in (
                "instagram",
                "instagram_reels"
            ):

                results["instagram"] = (
                    publish_instagram(
                        video_url,
                        description or title,
                    )
                )

            else:

                errors[platform] = (
                    "Unsupported platform."
                )

        except Exception as exc:

            errors[platform] = str(exc)

    status_code = (
        200
        if results
        else 502
    )

    return jsonify({
        "ok": bool(results),
        "results": results,
        "errors": errors,
    }), status_code


# ============================================================
# INDIVIDUAL PUBLISH ROUTES
# ============================================================

@app.route(
    "/publish/youtube",
    methods=["POST"]
)
def publish_youtube_route():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    video_url = str(
        payload.get(
            "video_url",
            ""
        )
    ).strip()

    if not video_url:
        return fail(
            "video_url is required."
        )

    try:

        result = publish_youtube(
            video_url,
            str(
                payload.get(
                    "title",
                    "BroWhat"
                )
            ),
            str(
                payload.get(
                    "description",
                    ""
                )
            ),
            payload.get(
                "tags"
            ) or [],
        )

        return ok(result)

    except Exception as exc:

        return fail(
            str(exc),
            502
        )


@app.route(
    "/publish/tiktok",
    methods=["POST"]
)
def publish_tiktok_route():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    video_url = str(
        payload.get(
            "video_url",
            ""
        )
    ).strip()

    if not video_url:
        return fail(
            "video_url is required."
        )

    try:

        result = publish_tiktok(
            video_url,
            str(
                payload.get(
                    "title",
                    "BroWhat"
                )
            ),
        )

        return ok(result)

    except Exception as exc:

        return fail(
            str(exc),
            502
        )


@app.route(
    "/publish/instagram",
    methods=["POST"]
)
def publish_instagram_route():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    video_url = str(
        payload.get(
            "video_url",
            ""
        )
    ).strip()

    if not video_url:
        return fail(
            "video_url is required."
        )

    try:

        result = publish_instagram(
            video_url,
            str(
                payload.get(
                    "caption",
                    payload.get(
                        "description",
                        payload.get(
                            "title",
                            "BroWhat"
                        )
                    ),
                )
            ),
        )

        return ok(result)

    except Exception as exc:

        return fail(
            str(exc),
            502
        )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=PORT
    )