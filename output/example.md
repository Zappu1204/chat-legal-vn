```sh
ubuntu@ip-172-31-27-114:~/chat-legal-vn$ source .venv/bin/activate
(chat-legal-vn) ubuntu@ip-172-31-27-114:~/chat-legal-vn$ chat-legal-vn ask "Đi xe máy vượt đèn đỏ và không đội mũ bảo hiểm bị phạt thế nào?"
/home/ubuntu/chat-legal-vn/src/chat_legal_vn/adapters/vectorstores/qdrant_store.py:169: UserWarning: Api key is used with an insecure connection.
  self._client = AsyncQdrantClient(
/home/ubuntu/chat-legal-vn/src/chat_legal_vn/adapters/docstore/qdrant_doc_store.py:36: UserWarning: Api key is used with an insecure connection.
  self._client = AsyncQdrantClient(
{"query": "\u0110i xe m\u00e1y v\u01b0\u1ee3t \u0111\u00e8n \u0111\u1ecf v\u00e0 kh\u00f4ng \u0111\u1ed9i m\u0169 b\u1ea3o hi\u1ec3m b\u1ecb ph\u1ea1t th\u1ebf n\u00e0o?", "sub_queries": ["\u0110i xe m\u00e1y v\u01b0\u1ee3t \u0111\u00e8n \u0111\u1ecf b\u1ecb ph\u1ea1t th\u1ebf n\u00e0o?", "\u0110i xe m\u00e1y kh\u00f4ng \u0111\u1ed9i m\u0169 b\u1ea3o hi\u1ec3m b\u1ecb ph\u1ea1t th\u1ebf n\u00e0o?"], "retrieved": 80, "reranked": 5, "expanded": 5, "retained": 5, "confidence": 0.0, "needs_human_review": true, "timings": {"plan": 16.37632555399614, "retrieve": 1.0454466239898466, "rerank": 2.24007967999205, "graph": 1.0830436070100404, "hydrate": 0.007459660992026329, "answer": 23.4152799359872, "resolve": 5.771098949480802e-05, "total": 44.16770962299779}, "event": "query.completed", "level": "info", "timestamp": "2026-05-31T12:32:30.226012Z"}
────────────────────────────────────────────────────── Câu trả lời ───────────────────────────────────────────────────────
Theo quy định hiện hành, hành vi vượt đèn đỏ có thể bị phạt tiền từ 4.000.000 đồng đến 6.000.000 đồng và bị trừ 2-4 điểm 
giấy phép lái xe. Hành vi không đội mũ bảo hiểm khi đi xe máy bị phạt tiền từ 400.000 đồng đến 600.000 đồng.
──────────────────────── Hành vi vượt đèn đỏ bị xử phạt và trừ điểm giấy phép lái xe như thế nào? ────────────────────────
Quy tắc: Không tìm thấy quy định đủ căn cứ trong dữ liệu
Áp dụng: Các tài liệu được cung cấp không chứa quy định cụ thể về mức phạt tiền và hình thức xử phạt bổ sung (trừ điểm) 
đối với hành vi vượt đèn đỏ. [Nghị định 168/2024/NĐ-CP - Điều 12 - Khoản 4] chỉ đề cập đến việc xử phạt người được chở 
trên xe ô tô không thắt dây đai an toàn.
Kết luận: Không thể xác định mức phạt cụ thể cho hành vi vượt đèn đỏ dựa trên các tài liệu được cung cấp.
────────────────────────── Hành vi không đội mũ bảo hiểm khi đi xe máy bị xử phạt như thế nào? ───────────────────────────
Quy tắc: Không tìm thấy quy định đủ căn cứ trong dữ liệu
Áp dụng: Các tài liệu được cung cấp không chứa quy định cụ thể về mức phạt tiền đối với người điều khiển hoặc người ngồi 
trên xe mô tô, xe gắn máy không đội mũ bảo hiểm hoặc đội mũ bảo hiểm không cài quai đúng quy cách. [Thông tư liên tịch 
06/2013/TTLT-BKHCN-BCT-BCA-BGTVT - Điều 10 - Khoản 1 - Điểm d] quy định về xử phạt vi phạm hành chính trong lĩnh vực kinh 
doanh mũ bảo hiểm, không phải quy định xử phạt người tham gia giao thông.
Kết luận: Không thể xác định mức phạt cụ thể cho hành vi không đội mũ bảo hiểm dựa trên các tài liệu được cung cấp.
───────────────────────────────────────────────────── Kết luận cuối ──────────────────────────────────────────────────────
Không tìm thấy quy định đủ căn cứ trong dữ liệu để trả lời câu hỏi về mức phạt đối với hành vi đi xe máy vượt đèn đỏ và 
không đội mũ bảo hiểm. Cần tham khảo các văn bản pháp luật chuyên ngành về xử phạt vi phạm hành chính trong lĩnh vực giao 
thông đường bộ (ví dụ: Nghị định quy định về xử phạt vi phạm hành chính trong lĩnh vực giao thông đường bộ) để có thông 
tin chính xác.
Thông tin này không thay thế tư vấn pháp lý chuyên nghiệp. Vui lòng tham khảo luật sư đối với các tình huống cụ thể.
(chat-legal-vn) ubuntu@ip-172-31-27-114:~/chat-legal-vn$ chat-legal-vn ask "Đi xe máy vượt đèn đỏ và không đội mũ bảo hiểm bị phạt thế nào?"
/home/ubuntu/chat-legal-vn/src/chat_legal_vn/adapters/vectorstores/qdrant_store.py:169: UserWarning: Api key is used with an insecure connection.
  self._client = AsyncQdrantClient(
/home/ubuntu/chat-legal-vn/src/chat_legal_vn/adapters/docstore/qdrant_doc_store.py:36: UserWarning: Api key is used with an insecure connection.
  self._client = AsyncQdrantClient(
{"query": "\u0110i xe m\u00e1y v\u01b0\u1ee3t \u0111\u00e8n \u0111\u1ecf v\u00e0 kh\u00f4ng \u0111\u1ed9i m\u0169 b\u1ea3o hi\u1ec3m b\u1ecb ph\u1ea1t th\u1ebf n\u00e0o?", "sub_queries": ["\u0110i xe m\u00e1y v\u01b0\u1ee3t \u0111\u00e8n \u0111\u1ecf b\u1ecb ph\u1ea1t th\u1ebf n\u00e0o?", "\u0110i xe m\u00e1y kh\u00f4ng \u0111\u1ed9i m\u0169 b\u1ea3o hi\u1ec3m b\u1ecb ph\u1ea1t th\u1ebf n\u00e0o?"], "retrieved": 80, "reranked": 5, "expanded": 5, "retained": 5, "confidence": 0.1, "needs_human_review": true, "timings": {"plan": 16.88187228399329, "retrieve": 1.4284305470064282, "rerank": 2.1102458959940122, "graph": 0.7286573690071236, "hydrate": 0.012102381006116048, "answer": 25.76641017399379, "resolve": 4.498100315686315e-05, "total": 46.9277805419988}, "event": "query.completed", "level": "info", "timestamp": "2026-05-31T12:42:34.211532Z"}
────────────────────────────────────────────────────── Câu trả lời ───────────────────────────────────────────────────────
Không tìm thấy quy định đủ căn cứ trong dữ liệu
───────────────────────────────────── Xử phạt hành vi điều khiển xe máy vượt đèn đỏ ──────────────────────────────────────
Quy tắc: Không tìm thấy quy định đủ căn cứ trong dữ liệu
Áp dụng: Các tài liệu được cung cấp không chứa quy định cụ thể về mức phạt đối với hành vi vượt đèn đỏ của người điều 
khiển xe máy. Tài liệu [5] về Nghị định 168/2024/NĐ-CP chỉ đề cập đến việc xử phạt người điều khiển xe máy chuyên dùng vi 
phạm quy tắc giao thông đường bộ (Điều 8) và điểm d Khoản 8 của điều này liên quan đến hành vi gây tai nạn giao thông, 
không phải là quy định về vượt đèn đỏ.
Kết luận: Không đủ căn cứ để trả lời về mức phạt cho hành vi vượt đèn đỏ.
Trích dẫn: [Nghị định 168/2024/NĐ-CP - Điều 8 - Khoản 8 - Điểm d]
──────────────────────────────── Xử phạt hành vi điều khiển xe máy không đội mũ bảo hiểm ─────────────────────────────────
Quy tắc: Không tìm thấy quy định đủ căn cứ trong dữ liệu
Áp dụng: Các tài liệu được cung cấp không chứa quy định cụ thể về mức phạt đối với hành vi không đội mũ bảo hiểm của người
điều khiển xe máy. Các tài liệu [1], [2], [4] thuộc Nghị định 109/2009/NĐ-CP về tín hiệu xe ưu tiên và tài liệu [3] về di 
sản văn hóa không liên quan đến vấn đề này.
Kết luận: Không đủ căn cứ để trả lời về mức phạt cho hành vi không đội mũ bảo hiểm.
───────────────────────────────────────────────────── Kết luận cuối ──────────────────────────────────────────────────────
Không tìm thấy quy định đủ căn cứ trong dữ liệu được cung cấp để trả lời câu hỏi về mức xử phạt cho hành vi đi xe máy vượt
đèn đỏ và không đội mũ bảo hiểm.
Cảnh báo:
[
    'Tài liệu được cung cấp không chứa các quy định cụ thể, chi tiết về xử phạt vi phạm giao thông đường bộ đối với hành 
vi vượt đèn đỏ và không đội mũ bảo hiểm. Cần tham chiếu trực tiếp đến Nghị định 168/2024/NĐ-CP hoặc các văn bản pháp luật 
có liên quan hiện hành để có thông tin chính xác.'
]
Thông tin này không thay thế tư vấn pháp lý chuyên nghiệp. Vui lòng tham khảo luật sư đối với các tình huống cụ thể.
(chat-legal-vn) ubuntu@ip-172-31-27-114:~/chat-legal-vn$ 
```