@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   考研闪卡启动器 - 从题库导出数据并打开
echo ========================================

cd /d "E:\Project\kaoyan-dashboard\src"

python -c "import sqlite3,json,os,webbrowser;db='E:/Project/kaoyan-dashboard/src/question_bank.db';conn=sqlite3.connect(db);conn.row_factory=sqlite3.Row;rows=conn.execute('SELECT q.id,q.topic_id,q.type,q.difficulty,q.source,q.content,c.state,c.difficulty as cd,c.stability,c.due_date,c.last_review,c.reps,c.lapses FROM questions q LEFT JOIN cards c ON c.question_id=q.id ORDER BY q.topic_id,q.id').fetchall();qs=[{'id':dict(r)['id'],'topic_id':dict(r)['topic_id'],'type':dict(r)['type'],'difficulty':dict(r)['difficulty'],'source':dict(r)['source'],'content':json.loads(dict(r)['content']),'card':{'state':dict(r).get('state') or 0,'difficulty':dict(r).get('cd') or 0,'stability':dict(r).get('stability') or 0,'due_date':dict(r).get('due_date') or '2026-07-12','last_review':dict(r).get('last_review'),'reps':dict(r).get('reps') or 0,'lapses':dict(r).get('lapses') or 0}} for r in rows];conn.close();data={'meta':{'generated':'auto','exam_date':'2026-12-19','total':len(qs)},'questions':qs};html=open('E:/Project/kaoyan-dashboard/src/flashcard_app.html','r',encoding='utf-8').read();inj='<script>window.__QUESTION_DATA__='+json.dumps(data,ensure_ascii=False)+';</script>';html=html.replace('</head>',inj+'\n</head>');out='E:/Project/kaoyan-dashboard/src/flashcard_ready.html';open(out,'w',encoding='utf-8').write(html);print(f'{len(qs)} questions loaded');webbrowser.open('file:///'+out.replace(chr(92),'/'))"

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python execution failed
    pause
) else (
    echo Flashcard app opened in browser!
    timeout /t 3 >nul
)
