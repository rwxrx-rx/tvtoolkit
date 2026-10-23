package com.tvdock.home;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RadialGradient;
import android.graphics.Shader;

import java.util.Random;

/** Draws nature scenes procedurally: no image files, no network, tiny APK. */
final class Scenery {
    static final int COUNT = 6;

    private static final class T {
        final int skyTop, skyMid, skyLow, glow, far, near;
        final float sx, sy;
        final boolean night, sea, mesa;

        T(int skyTop, int skyMid, int skyLow, int glow, float sx, float sy,
          int far, int near, boolean night, boolean sea, boolean mesa) {
            this.skyTop = skyTop; this.skyMid = skyMid; this.skyLow = skyLow; this.glow = glow;
            this.sx = sx; this.sy = sy; this.far = far; this.near = near;
            this.night = night; this.sea = sea; this.mesa = mesa;
        }
    }

    private static final T[] THEMES = {
        // alpine dawn
        new T(0xFF1E2A4A, 0xFF8A6A8E, 0xFFF6B27A, 0xFFFFE2A8, .70f, .50f, 0xFF8C7BA0, 0xFF1B2230, false, false, false),
        // forest mist
        new T(0xFF6FA6A0, 0xFFA9CBB7, 0xFFE3EBD0, 0xFFFFF6D0, .28f, .40f, 0xFF7FA58F, 0xFF16301F, false, false, false),
        // desert dusk
        new T(0xFF3A2A5C, 0xFFC4577A, 0xFFF8A65E, 0xFFFFD08A, .50f, .58f, 0xFFB5563F, 0xFF3A1D22, false, false, true),
        // ocean sunset
        new T(0xFF28305E, 0xFFE0707A, 0xFFFFC27A, 0xFFFFE6A8, .50f, .58f, 0xFF3B3F63, 0xFF11142A, false, true, false),
        // northern lights
        new T(0xFF050B1E, 0xFF0B1F3F, 0xFF16405A, 0x00000000, 0f, 0f, 0xFF16304A, 0xFF050A12, true, false, false),
        // glacier lake
        new T(0xFF4F86B8, 0xFF9CC6E0, 0xFFEAF3F5, 0xFFFFFFFF, .75f, .30f, 0xFF8FB0C8, 0xFF2D4A5E, false, true, false),
    };

    private Scenery() {}

    private static int lerp(int a, int b, float f) {
        int r = 0;
        for (int s = 0; s <= 24; s += 8) {
            int x = (a >> s) & 0xFF, y = (b >> s) & 0xFF;
            r |= ((int) (x + (y - x) * f) & 0xFF) << s;
        }
        return r;
    }

    private static void ridge(Canvas c, Paint p, int w, float baseY, float amp, long seed,
                              int top, int bottom, float bottomY, boolean mesa) {
        Random r = new Random(seed);
        float p1 = r.nextFloat() * 6.28f, p2 = r.nextFloat() * 6.28f, p3 = r.nextFloat() * 6.28f;
        float f1 = 0.8f + r.nextFloat() * 0.8f;
        Path path = new Path();
        path.moveTo(0, bottomY);
        float step = amp * 0.35f;
        for (int x = 0; x <= w; x += 8) {
            float t = x / (float) w * 6.2832f;
            float y = baseY - amp * (0.55f * (float) Math.sin(t * f1 + p1)
                    + 0.30f * (float) Math.sin(t * f1 * 2.3f + p2)
                    + 0.15f * (float) Math.sin(t * f1 * 5.1f + p3));
            if (mesa) y = Math.round(y / step) * step;
            path.lineTo(x, y);
        }
        path.lineTo(w, bottomY);
        path.close();
        p.setShader(new LinearGradient(0, baseY - amp, 0, bottomY, top, bottom, Shader.TileMode.CLAMP));
        c.drawPath(path, p);
    }

    private static void ridges(Canvas c, Paint p, int w, T t, float horizon, float amp, long seed, int alpha) {
        int layers = 4;
        for (int i = 0; i < layers; i++) {
            float f = i / (float) (layers - 1);
            float baseY = t.sea ? horizon + amp * 0.2f * (1 - f) : horizon + (1 - f) * -amp * 0.8f + f * amp * 0.9f;
            float a = amp * (1.0f - 0.45f * f);
            int col = lerp(t.far, t.near, f);
            int top = (col & 0x00FFFFFF) | (alpha << 24);
            int bot = (lerp(col, t.near, 0.55f) & 0x00FFFFFF) | (alpha << 24);
            if (t.sea && i > 1) break;
            ridge(c, p, w, baseY, a, seed + i * 977L, top, bot, horizon + amp * 1.2f, t.mesa);
            if (i < layers - 1 && alpha == 255) {
                int fog = t.skyLow & 0x00FFFFFF;
                p.setShader(new LinearGradient(0, baseY - a * 0.2f, 0, baseY + a * 0.9f,
                        fog, (0x55 << 24) | fog, Shader.TileMode.CLAMP));
                c.drawRect(0, baseY - a * 0.2f, w, baseY + a * 0.9f, p);
            }
        }
    }

    static Bitmap render(int w, int h, int idx, long seed) {
        T t = THEMES[Math.abs(idx) % COUNT];
        Bitmap b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(b);
        Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
        Random r = new Random(seed);
        float horizon = t.sea ? h * 0.64f : h * 0.70f;
        float amp = h * (t.sea ? 0.16f : 0.20f);

        p.setShader(new LinearGradient(0, 0, 0, horizon, new int[]{t.skyTop, t.skyMid, t.skyLow},
                new float[]{0f, 0.55f, 1f}, Shader.TileMode.CLAMP));
        c.drawRect(0, 0, w, h, p);

        if (t.night) {
            p.setShader(null);
            for (int i = 0; i < 240; i++) {
                p.setColor(((40 + r.nextInt(200)) << 24) | 0xFFFFFF);
                c.drawCircle(r.nextFloat() * w, r.nextFloat() * h * 0.6f, 0.6f + r.nextFloat() * 1.6f, p);
            }
            int[] ac = {0xFF3BFFB0, 0xFF35D9C9, 0xFF8A5CFF};
            for (int k = 0; k < 3; k++) {
                float y0 = h * (0.16f + 0.09f * k), am = h * 0.05f, ph = r.nextFloat() * 6f;
                float bandH = h * (0.24f - 0.04f * k);
                Path path = new Path();
                path.moveTo(0, y0);
                for (int x = 0; x <= w; x += 8)
                    path.lineTo(x, y0 + am * (float) Math.sin(x / (float) w * (4 + k) + ph));
                for (int x = w; x >= 0; x -= 8)
                    path.lineTo(x, y0 + bandH + am * (float) Math.sin(x / (float) w * (4 + k) + ph));
                path.close();
                int col = ac[k] & 0x00FFFFFF;
                p.setShader(new LinearGradient(0, y0 - am, 0, y0 + bandH + am,
                        new int[]{col, (0x99 << 24) | col, col}, new float[]{0f, 0.35f, 1f}, Shader.TileMode.CLAMP));
                c.drawPath(path, p);
            }
        } else {
            float cx = t.sx * w, cy = t.sy * h, rad = h * 0.40f;
            int g = t.glow & 0x00FFFFFF;
            p.setShader(new RadialGradient(cx, cy, rad, new int[]{(0xCC << 24) | g, (0x40 << 24) | g, g},
                    new float[]{0f, 0.35f, 1f}, Shader.TileMode.CLAMP));
            c.drawCircle(cx, cy, rad, p);
            p.setShader(null);
            p.setColor(t.glow);
            c.drawCircle(cx, cy, h * 0.04f, p);
        }

        long ridgeSeed = seed ^ 0x5DEECE66DL;
        ridges(c, p, w, t, horizon, amp, ridgeSeed, 255);

        if (t.sea) {
            p.setShader(new LinearGradient(0, horizon, 0, h, lerp(t.skyLow, t.near, 0.35f), t.near, Shader.TileMode.CLAMP));
            c.drawRect(0, horizon, w, h, p);
            c.save();
            c.clipRect(0, horizon, w, h);
            c.scale(1f, -1f, 0f, horizon);
            ridges(c, p, w, t, horizon, amp, ridgeSeed, 90);
            c.restore();
            if (!t.night) {
                float cx = t.sx * w;
                int g = t.glow & 0x00FFFFFF;
                p.setShader(new LinearGradient(0, horizon, 0, h, (0x99 << 24) | g, g, Shader.TileMode.CLAMP));
                c.drawRect(cx - w * 0.03f, horizon, cx + w * 0.03f, h, p);
            }
        } else {
            p.setShader(new LinearGradient(0, horizon, 0, h, lerp(t.near, 0xFF000000, 0.2f), t.near, Shader.TileMode.CLAMP));
            c.drawRect(0, horizon + amp * 0.2f, w, h, p);
        }

        p.setShader(new RadialGradient(w / 2f, h / 2f, Math.max(w, h) * 0.75f,
                new int[]{0x00000000, 0x00000000, 0x66000000}, new float[]{0f, 0.6f, 1f}, Shader.TileMode.CLAMP));
        c.drawRect(0, 0, w, h, p);
        return b;
    }
}
