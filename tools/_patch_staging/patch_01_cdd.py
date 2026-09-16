# -*- coding: utf-8 -*-
# 补丁 1：kaoyan-progress-sync 修 cd /d（cmd 语法在 pwsh 下失败）+ 版本记录
EDITS = [
    {
        "file": r"C:\Users\92534\.qoderworkcn\skills\kaoyan-progress-sync\SKILL.md",
        "replacements": [
            {
                "old": "```bash\ncd /d C:\\Users\\92534\\Desktop\\考研\\src\npython gap_analysis.py\npython daily_planner.py\npython generate_dashboard.py\n```",
                "new": "```bash\npython C:\\Users\\92534\\Desktop\\考研\\src\\gap_analysis.py\npython C:\\Users\\92534\\Desktop\\考研\\src\\daily_planner.py\npython C:\\Users\\92534\\Desktop\\考研\\src\\generate_dashboard.py\n```",
                "all": False,
            },
            {
                "old": "version: 1.0.0\n---\n\n# 考研学习进度同步",
                "new": "version: 1.1.0\n---\n\n# 考研学习进度同步",
                "all": False,
            },
        ],
    },
]
