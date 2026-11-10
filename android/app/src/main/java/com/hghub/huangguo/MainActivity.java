package com.hghub.huangguo;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * HGhub 复刻版 Android 壳：全屏竖屏 WebView。
 * 1.2.0.2：前端页面打进 APK 的 assets 里（assets/hghub/index.html +
 * hls.min.js），打开先加载本地页面（秒开、弱网也能看到界面），
 * 列表/播放再联网拉；相对路径以 APP_URL 为 base，API 仍走服务器。
 * 播放走直连 CDN（默认）或 8080 代理（备用）。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "hghub";
    private static final String KEY_URL = "app_url";

    private WebView webView;
    private FrameLayout fullscreenContainer;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 全屏沉浸
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN,
                WindowManager.LayoutParams.FLAG_FULLSCREEN);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        String url = savedUrl();
        if (url == null || url.isEmpty()) {
            showUrlSetup();
        } else {
            showWebView(url);
        }
    }

    private String savedUrl() {
        String fromPrefs = getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(KEY_URL, null);
        if (fromPrefs != null && !fromPrefs.isEmpty()) {
            return fromPrefs;
        }
        // 构建时填入的地址优先于空值
        String builtIn = BuildConfig.APP_URL;
        return (builtIn == null || builtIn.isEmpty()) ? null : builtIn;
    }

    /** 首次打开：输入复刻版网页地址一次，保存在本地。 */
    private void showUrlSetup() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad * 3, pad, pad);

        TextView title = new TextView(this);
        title.setText("HGhub");
        title.setTextSize(28);
        root.addView(title);

        TextView hint = new TextView(this);
        hint.setText("输入复刻版网页地址（部署后的地址），只输一次，之后直接打开刷剧。");
        hint.setPadding(0, pad, 0, pad);
        root.addView(hint);

        EditText input = new EditText(this);
        input.setHint("https://…");
        String builtIn = BuildConfig.APP_URL;
        if (builtIn != null && !builtIn.isEmpty()) {
            input.setText(builtIn);
        }
        root.addView(input);

        Button open = new Button(this);
        open.setText("打开");
        open.setOnClickListener(v -> {
            String url = input.getText().toString().trim();
            if (!url.isEmpty()) {
                if (!url.startsWith("http://") && !url.startsWith("https://")) {
                    url = "https://" + url;
                }
                getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                        .putString(KEY_URL, url).apply();
                showWebView(url);
            }
        });
        root.addView(open);

        Button clear = new Button(this);
        clear.setText("清除已保存地址");
        clear.setOnClickListener(v -> {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                    .remove(KEY_URL).apply();
            input.setText("");
        });
        root.addView(clear);

        setContentView(root);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void showWebView(String url) {
        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        fullscreenContainer = new FrameLayout(this);
        fullscreenContainer.setVisibility(View.GONE);
        root.addView(fullscreenContainer, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);                       // hls.js 需要
        s.setDomStorageEnabled(true);                       // 登录/历史/收藏
        s.setDatabaseEnabled(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setMediaPlaybackRequiresUserGesture(false);       // 刷到即播
        s.setAllowFileAccess(true);                     // 读 assets 里的本地页面
        s.setAllowFileAccessFromFileURLs(true);
        s.setAllowUniversalAccessFromFileURLs(true);    // 本地页可请求服务器 API
        s.setAllowContentAccess(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setTextZoom(100);

        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(webView, true);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                // 站内跳转留在 WebView 里
                view.loadUrl(url);
                return true;
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (customView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                customView = view;
                customViewCallback = callback;
                webView.setVisibility(View.GONE);
                fullscreenContainer.setVisibility(View.VISIBLE);
                fullscreenContainer.addView(view, new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT));
            }

            @Override
            public void onHideCustomView() {
                if (customView == null) {
                    return;
                }
                fullscreenContainer.removeView(customView);
                customView = null;
                fullscreenContainer.setVisibility(View.GONE);
                if (customViewCallback != null) {
                    customViewCallback.onCustomViewHidden();
                }
                webView.setVisibility(View.VISIBLE);
            }
        });

        loadBundledPage(url);
    }

    /**
     * 优先加载 APK 自带的本地页面（assets/hghub/index.html），以部署地址
     * 为 base URL，这样页面里的相对路径（/api/play、/proxy/*、/hls.min.js）
     * 仍解析到服务器；本地页面损坏或缺失时回退到直接联网加载。
     */
    private void loadBundledPage(String baseUrl) {
        try (InputStream in = getAssets().open("hghub/index.html")) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) != -1) {
                out.write(buf, 0, n);
            }
            String html = new String(out.toByteArray(), StandardCharsets.UTF_8);
            String base = baseUrl.endsWith("/") ? baseUrl : baseUrl + "/";
            webView.loadDataWithBaseURL(base, html, "text/html", "UTF-8", null);
        } catch (Exception e) {
            webView.loadUrl(baseUrl);
        }
    }

    @Override
    public void onBackPressed() {
        if (customView != null) {
            // 正在全屏播视频：先退出全屏
            new WebChromeClient().onHideCustomView();
            return;
        }
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) {
            webView.onPause();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.onResume();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
