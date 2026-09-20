// 复现用户浏览器里的现场：**上一版**把「就练这几张」的点名（ids）写进了 localStorage。
// 必须在页面脚本跑之前就位（探针表达式求值时已经 boot 完了，写进去也来不及）。
try {
  localStorage.setItem("kaoyan_flash_filter_v1", JSON.stringify({
    subject: "", bucket: "", topic: "",
    ids: ["C-STUDY-C882E54F", "C-STUDY-29DF6BAD", "C-STUDY-AA93743D", "C-STUDY-B8B6A730",
          "C-STUDY-54C47066", "C-STUDY-3917B2B8", "C-STUDY-239701A4", "C-STUDY-F5323C41",
          "C-STUDY-2397FDE4", "C-STUDY-88147B6E"],
  }));
} catch (e) {}
