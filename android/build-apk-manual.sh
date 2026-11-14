#!/usr/bin/env bash
# Manual APK build without Gradle (works where the Gradle daemon IPC fails).
#
# Requires: JDK 17 and Android SDK with platforms;android-34 and
# build-tools;34.0.0. Set JAVA_HOME and ANDROID_HOME, or edit the defaults.
# Produces: HGhub-1.2.0.9.apk in the android/ directory, signed with the
# fixed release keystore in ~/workspace/hghub-signing/ (never committed).
# 1.2.0.8：构建时把 web/index.html + web/hls.min.js 同步进
# app/src/main/assets/hghub/，打进 APK（本地页面秒开，API 仍走服务器）。
# 1.2.0.9：三条入口线路 + 固定发布签名（见下）。
set -euo pipefail

JAVA_HOME="${JAVA_HOME:-$HOME/workspace/tools/jdk17}"
ANDROID_HOME="${ANDROID_HOME:-$HOME/workspace/tools/android-sdk}"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/build-tools/34.0.0:$ANDROID_HOME/platform-tools:$PATH"

ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="$ROOT/app/src/main"
W="$(mktemp -d)"
trap 'rm -rf "$W"' EXIT
mkdir -p "$W"/{compiled-res,gen,classes,dex}

# 三条入口线路（按优先级），构建时注入，可用环境变量覆盖：
#   LINE1_URL="https://hg.45678.eu.org" bash android/build-apk-manual.sh
# 冷启动从第一条开始；当前线路失败（主框架加载失败或 5xx）自动顺延下一条；
# 三条都失败才转到应用内手动输入地址界面。
LINE1_URL="${LINE1_URL:-https://hg.45678.eu.org}"
LINE2_URL="${LINE2_URL:-https://hg1.45678.eu.org}"
LINE3_URL="${LINE3_URL:-https://sp1.45678.eu.org}"
# 转义成 Java 字符串字面量（防引号/反斜杠破坏代码）
LINE1_URL_JAVA=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1])[1:-1])' "$LINE1_URL")
LINE2_URL_JAVA=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1])[1:-1])' "$LINE2_URL")
LINE3_URL_JAVA=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1])[1:-1])' "$LINE3_URL")

mkdir -p "$W/gen/com/hghub/huangguo"
cat > "$W/gen/com/hghub/huangguo/BuildConfig.java" <<EOF
package com.hghub.huangguo;
public final class BuildConfig {
    public static final boolean DEBUG = true;
    public static final String APPLICATION_ID = "com.hghub.huangguo";
    public static final String BUILD_TYPE = "debug";
    public static final int VERSION_CODE = 1209;
    public static final String VERSION_NAME = "1.2.0.9";
    // 三条入口线路，构建时由环境变量 LINE1_URL/LINE2_URL/LINE3_URL 注入。
    public static final String LINE1_URL = "$LINE1_URL_JAVA";
    public static final String LINE2_URL = "$LINE2_URL_JAVA";
    public static final String LINE3_URL = "$LINE3_URL_JAVA";
}
EOF

# 前端打进 APK：每次构建都从 web/ 同步最新页面 + hls.js（仓库里也留一份）。
mkdir -p "$SRC/assets/hghub"
cp "$ROOT/../web/index.html" "$SRC/assets/hghub/index.html"
cp "$ROOT/../web/hls.min.js" "$SRC/assets/hghub/hls.min.js"
aapt2 compile --dir "$SRC/res" -o "$W/compiled-res.zip"
aapt2 link -o "$W/app-unsigned.apk" \
  -I "$ANDROID_HOME/platforms/android-34/android.jar" \
  --manifest "$SRC/AndroidManifest.xml" --java "$W/gen" \
  -A "$SRC/assets" \
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

# 固定发布签名：密钥库与密码都在仓库外的 ~/workspace/hghub-signing/，
# 绝不进仓库、绝不打进 APK 构建输入（只在签名这一步用）。不用临时 debug 密钥。
KEYSTORE="${KEYSTORE:-$HOME/workspace/hghub-signing/hghub-release.jks}"
KS_PASS_FILE="${KS_PASS_FILE:-$HOME/workspace/hghub-signing/keystore-password.txt}"
KS_ALIAS="${KS_ALIAS:-hghub}"
if [ ! -f "$KEYSTORE" ] || [ ! -f "$KS_PASS_FILE" ]; then
  echo "缺少发布密钥库或密码文件：$KEYSTORE / $KS_PASS_FILE" >&2
  exit 1
fi
zipalign -f -p 4 "$W/app-with-dex.apk" "$W/app-aligned.apk"
apksigner sign --ks "$KEYSTORE" --ks-pass "file:$KS_PASS_FILE" --key-pass "file:$KS_PASS_FILE" \
  --ks-key-alias "$KS_ALIAS" \
  --out "$ROOT/HGhub-1.2.0.9.apk" "$W/app-aligned.apk"
apksigner verify "$ROOT/HGhub-1.2.0.9.apk"
aapt2 dump badging "$ROOT/HGhub-1.2.0.9.apk" | grep -E "package:|sdkVersion|targetSdkVersion|launchable-activity"
ls -la "$ROOT/HGhub-1.2.0.9.apk"
unzip -l "$ROOT/HGhub-1.2.0.9.apk" | grep -E "assets/|classes.dex"
