package com.tvdock.home;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Outline;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.text.format.DateFormat;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.view.WindowManager;
import android.widget.AbsListView;
import android.widget.BaseAdapter;
import android.widget.FrameLayout;
import android.widget.GridView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

/**
 * Minimal Apple TV-style home screen: rotating nature wallpaper, a dock of favourite apps,
 * an "All apps" grid. No network, no background services, no dependencies.
 *
 * Favourites are set from `tvtool.py` with:  am start -n com.tvdock.home/.MainActivity --es favorites pkg1,pkg2
 * or on the TV: All apps > highlight an app > hold OK to add/remove it from the dock.
 * Your own photos: push JPG/PNG files to /sdcard/Android/data/com.tvdock.home/files/wallpapers/
 */
public class MainActivity extends Activity {
    private static final long ROTATE_MS = 10 * 60 * 1000L;
    private static final String PREFS = "dock";
    private static final String KEY_FAVS = "favorites";

    private static final class AppItem {
        final String pkg;
        final String label;
        AppItem(String pkg, String label) { this.pkg = pkg; this.label = label; }
    }

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final List<AppItem> apps = new ArrayList<>();
    private final List<String> favs = new ArrayList<>();
    private final Map<String, Drawable> icons = new HashMap<>();
    private final Random rnd = new Random();

    private PackageManager pm;
    private SharedPreferences prefs;
    private float density;
    private FrameLayout overlay;
    private ImageView bgBottom, bgTop;
    private LinearLayout dock;
    private TextView label, clock, date;
    private GridView grid;
    private AppsAdapter adapter;
    private int themeIdx = new Random().nextInt(Scenery.COUNT);
    private int photoIdx = 0;
    private boolean haveBackground = false;
    private boolean loading = false;

    private final Runnable rotateTask = new Runnable() {
        @Override public void run() { nextWallpaper(); }
    };
    private final Runnable clockTask = new Runnable() {
        @Override public void run() {
            Date now = new Date();
            clock.setText(DateFormat.getTimeFormat(MainActivity.this).format(now));
            date.setText(DateFormat.format("EEEE, d MMMM", now));
            ui.postDelayed(this, 20000);
        }
    };

    private int dp(float v) { return Math.round(v * density); }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        pm = getPackageManager();
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        density = getResources().getDisplayMetrics().density;
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED);
        buildUi();
        loadFavs();
        handleIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
        if (overlay.getVisibility() == View.VISIBLE) closeGrid();
    }

    @Override
    protected void onResume() {
        super.onResume();
        loadApps();
        rebuildDock();
        ui.post(clockTask);
        if (!haveBackground) nextWallpaper(); else ui.postDelayed(rotateTask, ROTATE_MS);
    }

    @Override
    protected void onPause() {
        super.onPause();
        ui.removeCallbacksAndMessages(null);  // nothing runs while another app is in front
    }

    @Override
    public void onBackPressed() {
        if (overlay.getVisibility() == View.VISIBLE) closeGrid();
        // otherwise do nothing: a home screen has nowhere to go back to
    }

    // ------------------------------------------------------------------ UI
    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);
        bgBottom = fullImage();
        bgTop = fullImage();
        bgTop.setAlpha(0f);
        root.addView(bgBottom);
        root.addView(bgTop);

        View scrim = new View(this);
        GradientDrawable g = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0x00000000, 0x00000000, 0x99000000});
        scrim.setBackground(g);
        root.addView(scrim, new FrameLayout.LayoutParams(-1, -1));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.VERTICAL);
        top.setGravity(Gravity.END);
        clock = text(34, true);
        date = text(16, false);
        clock.setGravity(Gravity.END);
        date.setGravity(Gravity.END);
        top.addView(clock);
        top.addView(date);
        FrameLayout.LayoutParams tp = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END);
        tp.setMargins(0, dp(28), dp(48), 0);
        root.addView(top, tp);

        label = text(20, true);
        label.setGravity(Gravity.CENTER);
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM);
        lp.setMargins(0, 0, 0, dp(148));
        root.addView(label, lp);

        dock = new LinearLayout(this);
        dock.setOrientation(LinearLayout.HORIZONTAL);
        dock.setGravity(Gravity.CENTER);
        dock.setPadding(dp(14), dp(10), dp(14), dp(10));
        GradientDrawable dockBg = new GradientDrawable();
        dockBg.setColor(0x55000000);
        dockBg.setCornerRadius(dp(34));
        dockBg.setStroke(dp(1), 0x33FFFFFF);
        dock.setBackground(dockBg);
        FrameLayout.LayoutParams dp0 = new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        dp0.setMargins(0, 0, 0, dp(32));
        root.addView(dock, dp0);

        overlay = new FrameLayout(this);
        overlay.setBackgroundColor(0xEE0E1116);
        overlay.setVisibility(View.GONE);
        TextView title = text(26, true);
        title.setText("All apps   (hold OK to add or remove from the dock)");
        FrameLayout.LayoutParams tl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        tl.setMargins(dp(64), dp(32), 0, 0);
        overlay.addView(title, tl);
        grid = new GridView(this);
        grid.setNumColumns(6);
        grid.setHorizontalSpacing(dp(12));
        grid.setVerticalSpacing(dp(12));
        grid.setPadding(dp(64), dp(96), dp(64), dp(32));
        grid.setClipToPadding(true);
        grid.setSelector(rounded(0x33FFFFFF, 0x99FFFFFF, 16));
        adapter = new AppsAdapter();
        grid.setAdapter(adapter);
        grid.setOnItemClickListener((p, v, pos, id) -> launch(apps.get(pos).pkg));
        grid.setOnItemLongClickListener((p, v, pos, id) -> {
            toggleFav(apps.get(pos).pkg);
            return true;
        });
        overlay.addView(grid, new FrameLayout.LayoutParams(-1, -1));
        root.addView(overlay, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
    }

    private ImageView fullImage() {
        ImageView v = new ImageView(this);
        v.setScaleType(ImageView.ScaleType.CENTER_CROP);
        v.setLayoutParams(new FrameLayout.LayoutParams(-1, -1));
        return v;
    }

    private TextView text(int sp, boolean bold) {
        TextView t = new TextView(this);
        t.setTextColor(Color.WHITE);
        t.setTextSize(sp);
        t.setShadowLayer(6, 0, 2, 0xAA000000);
        if (bold) t.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return t;
    }

    private GradientDrawable rounded(int fill, int stroke, int radiusDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(fill);
        d.setCornerRadius(dp(radiusDp));
        if (stroke != 0) d.setStroke(dp(2), stroke);
        return d;
    }

    // ------------------------------------------------------------------ apps and favourites
    private void loadApps() {
        apps.clear();
        Set<String> seen = new HashSet<>();
        for (String cat : new String[]{Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER}) {
            Intent i = new Intent(Intent.ACTION_MAIN).addCategory(cat);
            for (ResolveInfo ri : pm.queryIntentActivities(i, 0)) {
                String p = ri.activityInfo.packageName;
                if (p.equals(getPackageName()) || !seen.add(p)) continue;
                apps.add(new AppItem(p, String.valueOf(ri.loadLabel(pm))));
            }
        }
        Collections.sort(apps, (a, b) -> a.label.compareToIgnoreCase(b.label));
        adapter.notifyDataSetChanged();
    }

    private void loadFavs() {
        favs.clear();
        String s = prefs.getString(KEY_FAVS, null);
        if (s != null) for (String p : s.split(",")) if (!p.trim().isEmpty()) favs.add(p.trim());
    }

    private void saveFavs() {
        prefs.edit().putString(KEY_FAVS, TextUtils.join(",", favs)).apply();
    }

    private void handleIntent(Intent intent) {
        String f = intent == null ? null : intent.getStringExtra("favorites");
        if (f != null) {
            favs.clear();
            for (String p : f.split(",")) if (!p.trim().isEmpty()) favs.add(p.trim());
            saveFavs();
            rebuildDock();
        }
    }

    private void toggleFav(String pkg) {
        if (favs.remove(pkg)) toast("Removed from dock");
        else { favs.add(pkg); toast("Added to dock"); }
        saveFavs();
        rebuildDock();
        adapter.notifyDataSetChanged();
    }

    private void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_SHORT).show(); }

    private AppItem find(String pkg) {
        for (AppItem a : apps) if (a.pkg.equals(pkg)) return a;
        return null;
    }

    private Drawable iconOf(String pkg) {
        Drawable d = icons.get(pkg);
        if (d == null) {
            try { d = pm.getApplicationIcon(pkg); }
            catch (PackageManager.NameNotFoundException e) { d = getApplicationInfo().loadIcon(pm); }
            icons.put(pkg, d);
        }
        return d;
    }

    private void launch(String pkg) {
        Intent i = pm.getLeanbackLaunchIntentForPackage(pkg);
        if (i == null) i = pm.getLaunchIntentForPackage(pkg);
        try {
            if (i == null) throw new IllegalStateException();
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception e) {
            toast("Can't open that app");
        }
    }

    private void openSettings() {
        try {
            Intent i = new Intent(Settings.ACTION_SETTINGS);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception e) {
            toast("No settings app found");
        }
    }

    // ------------------------------------------------------------------ dock
    private void rebuildDock() {
        dock.removeAllViews();
        List<String> show = new ArrayList<>(favs);
        if (!prefs.contains(KEY_FAVS)) {  // first run before any choice: first few apps
            show.clear();
            for (AppItem a : apps) { if (show.size() < 6 && !a.pkg.contains("settings")) show.add(a.pkg); }
        }
        for (String p : show) {
            final AppItem a = find(p);
            if (a == null) continue;  // uninstalled or disabled: skip silently
            dock.addView(tile(iconOf(a.pkg), a.label, new View.OnClickListener() {
                @Override public void onClick(View v) { launch(a.pkg); }
            }));
        }
        dock.addView(tile(gridIcon(), "All apps", v -> openGrid()));
        Drawable s;
        try {
            ResolveInfo ri = pm.resolveActivity(new Intent(Settings.ACTION_SETTINGS), 0);
            s = ri != null ? ri.loadIcon(pm) : getApplicationInfo().loadIcon(pm);
        } catch (Exception e) { s = getApplicationInfo().loadIcon(pm); }
        dock.addView(tile(s, "Settings", v -> openSettings()));
        if (overlay.getVisibility() != View.VISIBLE) dock.getChildAt(0).requestFocus();
    }

    private View tile(Drawable icon, final String name, View.OnClickListener click) {
        FrameLayout f = new FrameLayout(this);
        f.setFocusable(true);
        f.setClickable(true);
        f.setPadding(dp(10), dp(10), dp(10), dp(10));
        ImageView iv = new ImageView(this);
        iv.setImageDrawable(icon);
        iv.setScaleType(ImageView.ScaleType.FIT_CENTER);
        iv.setOutlineProvider(new ViewOutlineProvider() {
            @Override public void getOutline(View v, Outline o) {
                o.setRoundRect(0, 0, v.getWidth(), v.getHeight(), dp(16));
            }
        });
        iv.setClipToOutline(true);
        f.addView(iv, new FrameLayout.LayoutParams(dp(72), dp(72)));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(92), dp(92));
        lp.setMargins(dp(4), 0, dp(4), 0);
        f.setLayoutParams(lp);
        f.setOnClickListener(click);
        f.setOnFocusChangeListener((v, focused) -> {
            v.animate().scaleX(focused ? 1.22f : 1f).scaleY(focused ? 1.22f : 1f).setDuration(120).start();
            v.setBackground(focused ? rounded(0x44FFFFFF, 0xCCFFFFFF, 22) : null);
            if (focused) label.setText(name);
        });
        return f;
    }

    private Drawable gridIcon() {
        int s = 256;
        Bitmap b = Bitmap.createBitmap(s, s, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(b);
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        p.setColor(0xFF2B2B30);
        c.drawRoundRect(0, 0, s, s, 56, 56, p);
        p.setColor(Color.WHITE);
        float cell = s / 5f;
        for (int i = 1; i <= 3; i++)
            for (int j = 1; j <= 3; j++)
                c.drawRoundRect(cell * i + cell * 0.15f, cell * j + cell * 0.15f,
                        cell * i + cell * 0.85f, cell * j + cell * 0.85f, 10, 10, p);
        return new BitmapDrawable(getResources(), b);
    }

    // ------------------------------------------------------------------ all apps overlay
    private void openGrid() {
        overlay.setVisibility(View.VISIBLE);
        grid.requestFocus();
    }

    private void closeGrid() {
        overlay.setVisibility(View.GONE);
        if (dock.getChildCount() > 0) dock.getChildAt(0).requestFocus();
    }

    private final class AppsAdapter extends BaseAdapter {
        @Override public int getCount() { return apps.size(); }
        @Override public Object getItem(int i) { return apps.get(i); }
        @Override public long getItemId(int i) { return i; }

        @Override
        public View getView(int pos, View convert, ViewGroup parent) {
            LinearLayout row;
            ImageView iv;
            TextView tv;
            if (convert == null) {
                row = new LinearLayout(MainActivity.this);
                row.setOrientation(LinearLayout.VERTICAL);
                row.setGravity(Gravity.CENTER);
                row.setPadding(dp(8), dp(12), dp(8), dp(12));
                iv = new ImageView(MainActivity.this);
                row.addView(iv, new LinearLayout.LayoutParams(dp(64), dp(64)));
                tv = text(14, false);
                tv.setSingleLine(true);
                tv.setEllipsize(TextUtils.TruncateAt.END);
                tv.setGravity(Gravity.CENTER);
                row.addView(tv, new LinearLayout.LayoutParams(-1, -2));
                row.setLayoutParams(new AbsListView.LayoutParams(-1, dp(120)));
            } else {
                row = (LinearLayout) convert;
                iv = (ImageView) row.getChildAt(0);
                tv = (TextView) row.getChildAt(1);
            }
            AppItem a = apps.get(pos);
            iv.setImageDrawable(iconOf(a.pkg));
            tv.setText((favs.contains(a.pkg) ? "★ " : "") + a.label);
            return row;
        }
    }

    // ------------------------------------------------------------------ wallpapers
    private void nextWallpaper() {
        if (loading) return;
        loading = true;
        final int theme = themeIdx++;
        final long seed = rnd.nextLong();
        final File[] photos = photoFiles();
        final int photo = photoIdx++;
        new Thread(new Runnable() {
            @Override public void run() {
                Bitmap b = null;
                if (photos.length > 0) b = decode(photos[photo % photos.length]);
                if (b == null) b = Scenery.render(1280, 720, theme, seed);
                final Bitmap result = b;
                ui.post(new Runnable() {
                    @Override public void run() {
                        loading = false;
                        if (isDestroyed()) return;
                        crossfade(result);
                    }
                });
            }
        }).start();
    }

    private File[] photoFiles() {
        File dir = getExternalFilesDir("wallpapers");
        File[] fs = dir == null ? null : dir.listFiles((d, n) -> {
            String l = n.toLowerCase();
            return l.endsWith(".jpg") || l.endsWith(".jpeg") || l.endsWith(".png") || l.endsWith(".webp");
        });
        if (fs == null) return new File[0];
        Arrays.sort(fs);
        return fs;
    }

    private Bitmap decode(File f) {
        try {
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            BitmapFactory.decodeFile(f.getAbsolutePath(), o);
            int s = 1;
            while (o.outWidth / (s * 2) >= 1920) s *= 2;
            o = new BitmapFactory.Options();
            o.inSampleSize = s;
            return BitmapFactory.decodeFile(f.getAbsolutePath(), o);
        } catch (Throwable t) {
            return null;
        }
    }

    private void crossfade(final Bitmap b) {
        if (!haveBackground) {
            bgBottom.setImageBitmap(b);
            haveBackground = true;
        } else {
            bgTop.setImageBitmap(b);
            bgTop.setAlpha(0f);
            bgTop.animate().alpha(1f).setDuration(1500).withEndAction(new Runnable() {
                @Override public void run() {
                    bgBottom.setImageBitmap(b);
                    bgTop.setAlpha(0f);
                    bgTop.setImageDrawable(null);
                }
            }).start();
        }
        ui.removeCallbacks(rotateTask);
        ui.postDelayed(rotateTask, ROTATE_MS);
    }
}
