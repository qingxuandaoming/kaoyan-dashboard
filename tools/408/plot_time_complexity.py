import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams['font.family'] = 'Microsoft YaHei'
plt.rcParams['axes.unicode_minus'] = False

C_LOG = '#8A9AA8'
C_N = '#C9A07C'
C_NLOG = '#B08A8A'
C_TEXT = '#3D3D3D'
C_GRID = '#E3E3E3'
C_BORDER = '#8A9AA8'

OUT = r'C:\Users\92534\Desktop\考研\408\专题\assets\DS_时间复杂度增长曲线对比.png'

fig, ax = plt.subplots(figsize=(10, 6.2), dpi=200)
fig.patch.set_facecolor('white')
ax.set_facecolor('white')

n = np.linspace(1, 64, 640)
log_n = np.log2(n)
lin = n
nlog = n * np.log2(n)

ax.plot(n, nlog, color=C_NLOG, lw=2.4, zorder=3)
ax.plot(n, lin, color=C_N, lw=2.4, zorder=3)
ax.plot(n, log_n, color=C_LOG, lw=2.4, zorder=3)

for xv, yv, c in [(64, 384, C_NLOG), (64, 64, C_N), (64, 6, C_LOG)]:
    ax.plot([xv], [yv], 'o', color=c, ms=5, zorder=4)

ax.text(65.8, 384, '$n\\,\\log_2 n$', color=C_NLOG, fontsize=12, va='center', fontweight='bold')
ax.text(65.8, 64, '$n$', color=C_N, fontsize=12, va='center', fontweight='bold')
ax.text(65.8, 6, '$\\log_2 n$', color=C_LOG, fontsize=12, va='center', fontweight='bold')

ax.text(28, 12, 'log n 增长极慢（n×8，值仅×2）', fontsize=9.5, color=C_LOG, ha='center')

ax.text(38.5, 327, 'n·log n ÷ n = log n', color=C_NLOG, fontsize=10,
        fontweight='bold', ha='center')
ax.text(38.5, 316, '比值随 n 缓慢增大（n=64 时仅 6 倍）', color=C_TEXT, fontsize=9.5, ha='center')

ax.plot([2], [2], 'o', color=C_NLOG, ms=4, zorder=4)
ax.annotate('n = 2 时 n·log n = n（两线相交）\n此后 n·log n 永久大于 n',
            xy=(2, 2), xytext=(14, 125), fontsize=9.5, color=C_TEXT, ha='center',
            arrowprops=dict(arrowstyle='->', color=C_BORDER, lw=1.4,
                           connectionstyle='arc3,rad=-0.15'))

tbl = ax.table(
    cellText=[['$\\log_2 n$', '3', '4', '5', '6'],
              ['$n$', '8', '16', '32', '64'],
              ['$n\\,\\log_2 n$', '24', '64', '160', '384']],
    colLabels=['$n$', '8', '16', '32', '64'],
    cellLoc='center', bbox=[0.02, 0.55, 0.315, 0.37])
tbl.auto_set_font_size(False)
tbl.set_fontsize(10)

row_label_colors = {1: C_LOG, 2: C_N, 3: C_NLOG}
for (r, c), cell in tbl.get_celld().items():
    cell.set_linewidth(0.8)
    cell.set_edgecolor('#B8C4CE')
    if c == 0:
        cell.set_width(0.40)
        if r in row_label_colors:
            cell.set_facecolor('#F0F0F0')
            cell.get_text().set_color(row_label_colors[r])
            cell.get_text().set_fontweight('bold')
    else:
        cell.set_width(0.15)
        if r == 0:
            cell.set_facecolor('#D6DFE8')
            cell.get_text().set_color(C_TEXT)
            cell.get_text().set_fontweight('bold')
        else:
            cell.set_facecolor('white')
            cell.get_text().set_color(C_TEXT)

ax.set_xlim(0, 76)
ax.set_ylim(0, 420)
ax.set_xticks([1, 2, 4, 8, 16, 32, 64])
ax.set_yticks([0, 100, 200, 300, 400])
ax.grid(True, color=C_GRID, lw=0.8, zorder=0)
ax.set_axisbelow(True)
for side in ['top', 'right']:
    ax.spines[side].set_visible(False)
for side in ['left', 'bottom']:
    ax.spines[side].set_color(C_BORDER)
ax.tick_params(colors='#6B6B6B', labelsize=10)
ax.set_xlabel('问题规模 n', fontsize=11, color=C_TEXT)
ax.set_ylabel('基本操作执行次数 T(n)', fontsize=11, color=C_TEXT)
ax.set_title('时间复杂度增长对比：$O(\\log n)\\ \\prec\\ O(n)\\ \\prec\\ O(n\\,\\log n)$',
             fontsize=13.5, color=C_TEXT, pad=12)

fig.text(0.5, 0.012,
         '注：图中对数以 2 为底（底数为常数，不影响量级，统一记 O(log n)）；n > 2 时恒有 log n < n < n·log n。',
         fontsize=9, color='#6B6B6B', ha='center')

plt.savefig(OUT, dpi=200, bbox_inches='tight', pad_inches=0.15, facecolor='white')
print('saved:', OUT)
