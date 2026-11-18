"use strict";
document.querySelector("#run").addEventListener("click",async()=>{
  const result=document.querySelector("#result");
  result.textContent="Đang kiểm tra file input cục bộ…";
  try {
    const reply=await chrome.runtime.sendMessage({type:"probe_file_input",
      path:document.querySelector("#path").value.trim(),
      expectedSize:Number(document.querySelector("#size").value)});
    result.textContent=reply?.ok ? `Đã nhận ${reply.filename} (${reply.bytes} byte).` :
      `Lỗi: ${reply?.error || "Không có phản hồi."}`;
  } catch(error) {result.textContent=`Lỗi: ${error.message}`;}
});
