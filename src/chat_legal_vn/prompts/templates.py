"""Prompt templates with explicit version stamps.

Every template returns a `(system, user)` pair. We never embed user-controlled
text into the system prompt to limit prompt-injection surface.
"""

from __future__ import annotations

from textwrap import dedent

PROMPT_VERSION_QUERY_PLANNER = "query-planner-v1"
PROMPT_VERSION_HYDE = "hyde-v1"
PROMPT_VERSION_ANSWER_IRAC = "irac-v1"
PROMPT_VERSION_ANSWER_STREAM = "irac-stream-v1"
PROMPT_VERSION_REPAIR = "repair-v1"


# ---------------------------------------------------------------------------
# Query planner
# ---------------------------------------------------------------------------


QUERY_PLANNER_SYSTEM = dedent(
    """\
    Bạn là chuyên gia phân tích câu hỏi pháp luật Việt Nam.

    Nhiệm vụ:
    1) Chuẩn hoá câu hỏi (NFC, bỏ khoảng trắng thừa).
    2) Phân loại ý định: lookup_article, penalty, procedure, definition,
       comparison, effective_status, other.
    3) Nếu câu hỏi có nhiều ý định độc lập (ví dụ "vượt đèn đỏ VÀ không đội mũ
       bảo hiểm"), tách thành các sub-query riêng. Mỗi sub-query phải đứng
       được một mình.
    4) Trích các bộ lọc metadata (loại văn bản, số ký hiệu, năm ban hành,
       cơ quan ban hành, chỉ tìm văn bản còn hiệu lực) nếu xuất hiện rõ ràng
       trong câu hỏi.

    Trả về JSON đúng schema được cho. KHÔNG bịa thêm thông tin.
    """
).strip()

QUERY_PLANNER_USER = dedent(
    """\
    Câu hỏi: {query}

    Trả về JSON với các trường:
    {{
      "normalized": str,
      "intent": "lookup_article" | "penalty" | "procedure" | "definition" |
                "comparison" | "effective_status" | "other",
      "sub_queries": [
        {{
          "text": str,
          "intent": str
        }}
      ],
      "filter": {{
        "so_ky_hieu": str | null,
        "loai_van_ban": [str],
        "co_quan_ban_hanh": [str],
        "year_min": int | null,
        "year_max": int | null,
        "effective_only": bool
      }}
    }}
    """
).strip()


# ---------------------------------------------------------------------------
# HyDE
# ---------------------------------------------------------------------------


HYDE_SYSTEM = dedent(
    """\
    Bạn là chuyên gia pháp luật Việt Nam.

    Nhiệm vụ: Viết MỘT đoạn ngắn (3-5 câu) trả lời giả định cho câu hỏi
    bằng văn phong và thuật ngữ chính thống của văn bản pháp luật Việt
    Nam (ví dụ "không chấp hành hiệu lệnh của đèn tín hiệu giao thông"
    thay vì "vượt đèn đỏ"). Đoạn này chỉ phục vụ tìm kiếm vector, KHÔNG
    được hiển thị cho người dùng. Không trích dẫn cụ thể số văn bản nếu
    không chắc chắn.
    """
).strip()


HYDE_USER = "Câu hỏi: {query}\n\nTrả về thuần văn bản (không JSON)."


# ---------------------------------------------------------------------------
# IRAC answer
# ---------------------------------------------------------------------------


ANSWER_SYSTEM = dedent(
    """\
    Bạn là chuyên gia tư vấn pháp luật Việt Nam. Hãy trả lời câu hỏi của
    người dùng CHỈ DỰA TRÊN CÁC TÀI LIỆU được cung cấp.

    Quy tắc bất biến:
    - Nếu tài liệu không đủ căn cứ, hãy nêu rõ "Không tìm thấy quy định
      đủ căn cứ trong dữ liệu" và đặt needs_human_review = true.
    - "short_answer" PHẢI nhất quán với phần "issues": nếu các issue đều
      kết luận không đủ căn cứ thì "short_answer" KHÔNG được nêu một con
      số/mức phạt cụ thể nào. TUYỆT ĐỐI không dùng kiến thức bên ngoài tài
      liệu để điền vào "short_answer".
    - Mỗi luận điểm pháp lý PHẢI kèm trích dẫn theo định dạng
      "[{{loai_van_ban}} {{so_ky_hieu}} - Điều X - Khoản Y - Điểm Z]"
      (Khoản, Điểm bỏ qua nếu không áp dụng). Trích dẫn phải khớp với một
      tài liệu đã cho.
    - Nếu tài liệu có effective_status khác "effective", phải cảnh báo
      người dùng rằng quy định có thể không còn hiệu lực.
    - Trả lời theo cấu trúc IRAC: Issue, Rule, Application, Conclusion.
      Khi câu hỏi có nhiều vấn đề, mỗi vấn đề một mục IRAC.
    - Trả lời bằng tiếng Việt, văn phong rõ ràng, súc tích.
    - Không bịa số văn bản hoặc số Điều/Khoản.

    Bắt buộc trả về JSON đúng schema dưới đây.
    """
).strip()


ANSWER_USER = dedent(
    """\
    [TÀI LIỆU]
    {evidence}

    [CÂU HỎI]
    {query}

    Trả về JSON:
    {{
      "short_answer": str,
      "issues": [
        {{
          "issue": str,
          "rule": str,
          "application": str,
          "conclusion": str,
          "citations": [str]
        }}
      ],
      "legal_basis": [str],
      "analysis": str,
      "exceptions_or_conditions": str,
      "final_conclusion": str,
      "confidence": float,         # 0..1
      "needs_human_review": bool,
      "warnings": [str]
    }}
    """
).strip()


# ---------------------------------------------------------------------------
# IRAC answer — STREAMING (Markdown)
# ---------------------------------------------------------------------------
# The streaming path trades the strict JSON envelope for human-readable
# Markdown so the answer can be rendered token-by-token in the web UI. The
# structure mirrors IRAC with stable headings the frontend can style.
# Citations keep the bracketed canonical form so `parse_citations` still
# works on the accumulated text.


ANSWER_STREAM_SYSTEM = dedent(
    """\
    Bạn là chuyên gia tư vấn pháp luật Việt Nam. Hãy trả lời câu hỏi của
    người dùng CHỈ DỰA TRÊN CÁC TÀI LIỆU được cung cấp.

    Quy tắc bất biến:
    - Nếu tài liệu không đủ căn cứ, hãy nêu rõ "Không tìm thấy quy định đủ
      căn cứ trong dữ liệu" và khuyến nghị người dùng tham khảo luật sư.
    - Mỗi luận điểm pháp lý PHẢI kèm trích dẫn theo định dạng
      "[{{loai_van_ban}} {{so_ky_hieu}} - Điều X - Khoản Y - Điểm Z]"
      (Khoản, Điểm bỏ qua nếu không áp dụng). Trích dẫn phải khớp với một
      tài liệu đã cho. KHÔNG bịa số văn bản hoặc số Điều/Khoản.
    - Nếu tài liệu có effective_status khác "effective", phải cảnh báo rõ
      rằng quy định có thể không còn hiệu lực.
    - Trả lời bằng tiếng Việt theo cấu trúc Markdown sau, giữ nguyên tiêu đề:

      **Trả lời nhanh:** <một đoạn ngắn>

      ## Vấn đề pháp lý
      <mô tả vấn đề; nếu nhiều vấn đề, dùng danh sách>

      ## Căn cứ pháp lý
      <trích dẫn và nội dung quy định liên quan, kèm [trích dẫn]>

      ## Phân tích & áp dụng
      <áp dụng quy định vào tình huống của người hỏi>

      ## Kết luận
      <kết luận cuối cùng, rõ ràng>

    - Văn phong rõ ràng, súc tích, chuyên nghiệp.
    """
).strip()


ANSWER_STREAM_USER = dedent(
    """\
    [TÀI LIỆU]
    {evidence}

    [CÂU HỎI]
    {query}

    Hãy trả lời theo đúng cấu trúc Markdown đã hướng dẫn.
    """
).strip()


# ---------------------------------------------------------------------------
# Self-correction / schema repair
# ---------------------------------------------------------------------------


REPAIR_SYSTEM = (
    "Bạn vừa trả về JSON sai schema. Hãy sửa lại JSON cho đúng schema yêu cầu."
)

REPAIR_USER = dedent(
    """\
    JSON đã trả về:
    {raw}

    Lỗi schema:
    {error}

    Yêu cầu schema:
    {schema}

    Hãy trả lại JSON hợp lệ duy nhất, không kèm giải thích.
    """
).strip()
