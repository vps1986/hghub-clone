#!/usr/bin/env bash
# Manual APK build without Gradle (works where the Gradle daemon IPC fails).
#
# Requires: JDK 17 and Android SDK with platforms;android-34 and
# build-tools;34.0.0. Set JAVA_HOME and ANDROID_HOME, or edit the defaults.
# Produces: HGhub-1.2.0.apk (debug-signed) in the current directory.
set -euo pipefail

JAVA_HOME="${JAVA_HOME:-$HOME/workspace/.build-tmp/jdk17}"
ANDROID_HOME="${ANDROID_HOME:-$HOME/workspace/.build-tmp/android-sdk}"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/build-tools/34.0.0:$ANDROID_HOME/platform-tools:$PATH"

ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="$ROOT/app/src/main"
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
mkdir -p "$W"/{compiled-res,gen,classes,dex}

# APP_URL：复刻版网页的部署地址，构建时注入，仓库里不写死服务器 IP。
#   APP_URL="http://YOUR_SERVER_IP:8081/" bash android/build-apk-manual.sh
# 不传则默认值为空，首次打开时在应用内输入一次（保存在本地）。
APP_URL="${APP_URL:-}"
# 转义成 Java 字符串字面量（防引号/反斜杠破坏代码）
APP_URL_JAVA=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1])[1:-1])' "$APP_URL")

mkdir -p "$W/gen/com/hghub/huangguo"
cat > "$W/gen/com/hghub/huangguo/BuildConfig.java" <<EOF
package com.hghub.huangguo;
public final class BuildConfig {
    public static final boolean DEBUG = true;
    public static final String APPLICATION_ID = "com.hghub.huangguo";
    public static final String BUILD_TYPE = "debug";
    public static final int VERSION_CODE = 120;
    public static final String VERSION_NAME = "1.2.0";
    // 构建时由环境变量 APP_URL 注入；为空则首次打开时在应用内输入一次。
    public static final String APP_URL = "$APP_URL_JAVA";
}
EOF

aapt2 compile --dir "$SRC/res" -o "$W/compiled-res.zip"
aapt2 link -o "$W/app-unsigned.apk" \
  -I "$ANDROID_HOME/platforms/android-34/android.jar" \
  --manifest "$SRC/AndroidManifest.xml" --java "$W/gen" \
  --min-sdk-version 24 --target-sdk-version 34 "$W/compiled-res.zip"
javac -encoding UTF-8 -cp "$ANDROID_HOME/platforms/android-34/android.jar" \
  -d "$W/classes" $(find "$W/gen" "$SRC/java" -name '*.java')
(cd "$W/classes" && jar --create --file "$W/classes.jar" .)
d8 --output "$W/dex" --lib "$ANDROID_HOME/platforms/android-34/android.jar" "$W/classes.jar"

cp "$W/app-unsigned.apk" "$W/app-with-dex.apk"
python3 - "$W" <<'PY'
import sys, zipfile
w = sys.argv[1]
z = zipfile.ZipFile(f"{w}/app-with-dex.apk", "a", zipfile.ZIP_DEFLATED)
z.write(f"{w}/dex/classes.dex", "classes.dex")
z.close()
PY

KEYSTORE="${DEBUG_KEYSTORE:-$W/debug.keystore}"
if [ ! -f "$KEYSTORE" ]; then
  keytool -genkeypair -keystore "$KEYSTORE" -storepass android -keypass android \
    -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Android Debug,O=Android,C=US" -noprompt
fi
zipalign -f -p 4 "$W/app-with-dex.apk" "$W/app-aligned.apk"
apksigner sign --ks "$KEYSTORE" --ks-pass pass:android --key-pass pass:android \
  --out "$ROOT/HGhub-1.2.0.apk" "$W/app-aligned.apk"
apksigner verify "$ROOT/HGhub-1.2.0.apk"
aapt2 dump badging "$ROOT/HGhub-1.2.0.apk" | grep -E "package:|sdkVersion|targetSdkVersion|launchable-activity"
ls -la "$ROOT/HGhub-1.2.0.apk"
