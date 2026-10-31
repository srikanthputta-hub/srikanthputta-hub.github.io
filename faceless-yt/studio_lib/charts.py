"""Animated, branded charts built from the real data (matplotlib frames -> ffmpeg)."""
import shutil
import subprocess
from pathlib import Path

NAVY, GRID, GOLD, WHITE, MUTED = "#0B1F3A", "#1E4A80", "#F5B82E", "#FFFFFF", "#9FB3CF"
PALETTE = [GOLD, "#4DD0E1", "#FF7043", "#C5E1A5"]


def _ease(p):
    return 1 - (1 - p) ** 3


def render_chart_video(chart, out, work, font_path=None, size=(1920, 1080), reveal_s=4.5, hold_s=3.0, fps=15):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import FuncFormatter

    if font_path:
        try:
            font_manager.fontManager.addfont(font_path)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
        except Exception:
            pass
    work = Path(work)
    frames_dir = work / f"frames_{chart['id']}"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    W, H = size
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=NAVY)
    ax = fig.add_axes([0.075, 0.25, 0.86, 0.57], facecolor=NAVY)  # bottom band stays free for captions
    xs = [p[0] for ln in chart["lines"] for p in ln["points"]]
    ys = [p[1] for ln in chart["lines"] for p in ln["points"]]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.12 or 1
    ax.set_xlim(min(xs), max(xs) + (max(xs) - min(xs)) * 0.07)
    ax.set_ylim(lo - pad, hi + pad)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="y", color=GRID, alpha=0.55, linewidth=1.2)
    ax.tick_params(colors=MUTED, labelsize=22, length=0)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x + 1e-6)}"))
    if lo < 0 < hi:
        ax.axhline(0, color=MUTED, linewidth=1.6, alpha=0.8)
    fig.text(0.075, 0.915, chart["title"], fontsize=44, color=WHITE, fontweight="bold")
    unit_name = "Percent" if chart["unit"] == "%" else chart["unit"]
    suffix = "%" if chart["unit"] == "%" else ""
    fig.text(0.075, 0.855, f"{unit_name}  ·  {chart['why'][:90]}" if chart.get("why") else unit_name, fontsize=24, color=MUTED)
    fig.text(0.075, 0.185, f"Source: {chart['source']}", fontsize=20, color=MUTED)
    fig.text(0.935, 0.185, "THE MONEY BLUEPRINT", fontsize=20, color=GOLD, ha="right", fontweight="bold")

    artists = []
    for i, ln in enumerate(chart["lines"]):
        col = PALETTE[i % len(PALETTE)]
        (line,) = ax.plot([], [], color=col, linewidth=5, solid_capstyle="round", label=ln["label"])
        (dot,) = ax.plot([], [], "o", color=col, markersize=14)
        artists.append((ln, line, dot, col))
    if len(chart["lines"]) > 1:
        ax.legend(loc="upper left", frameon=False, fontsize=24, labelcolor=WHITE)

    n_frames = max(2, int(reveal_s * fps) + 1)
    labels = []
    for k in range(n_frames):
        frac = _ease(k / (n_frames - 1))
        for t in labels:
            t.remove()
        labels = []
        for ln, line, dot, col in artists:
            pts = ln["points"]
            m = max(2, int(round(frac * len(pts))))
            line.set_data([p[0] for p in pts[:m]], [p[1] for p in pts[:m]])
            dot.set_data([pts[m - 1][0]], [pts[m - 1][1]])
            labels.append(ax.annotate(f"{pts[m - 1][1]:,.1f}{suffix}", (pts[m - 1][0], pts[m - 1][1]), xytext=(14, 10),
                                      textcoords="offset points", color=col, fontsize=28, fontweight="bold"))
        fig.savefig(frames_dir / f"f_{k:04d}.png", facecolor=NAVY)
    plt.close(fig)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "f_%04d.png"),
                    "-vf", f"scale={W}:{H},tpad=stop_mode=clone:stop_duration={hold_s},fps=30,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", str(out)], check=True, capture_output=True)
    shutil.rmtree(frames_dir, ignore_errors=True)
    return Path(out)
