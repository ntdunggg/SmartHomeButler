# Role × 100-request live eval

Mỗi thành viên trong gia đình được thử **100 request mới hoàn toàn, giống hệt nhau** qua
LLM thật (`gpt-4o-mini`) để so sánh **cùng câu nói → khác kết quả tuỳ vai trò/độ tuổi**.

- 4 role × 100 = **400 request LLM thật**, chạy tuần tự để tránh rate-limit.
- **0 lỗi, 0 abstain** (bản sạch — lần chạy song song trước bị rate-limit nên đã bỏ).
- Pipeline: multi-agent production (hiểu + suy luận + kế hoạch qua LLM) →
  `build_executable_steps` (chấm quyền theo `role`/`age_tier`). **Không** execute lên thiết bị:
  mỗi request độc lập, cùng trạng thái seed → so sánh công bằng giữa các role.

## File

| File | Vai trò |
|---|---|
| `bo_owner_adult.json` | bố — OWNER / adult |
| `me_owner_adult.json` | mẹ — OWNER / adult |
| `con_lon_member_teen.json` | con lớn — member / teen |
| `con_nho_member_child.json` | con nhỏ — member / child |
| `requests100.py` | 100 câu dùng chung (10 category) |
| `worker.py` | runner 1 role (tham chiếu; đường dẫn scratchpad cũ) |
| `aggregate.py` | tổng hợp — chạy được từ repo, đọc 4 file cạnh nó |
| `REPORT.txt` | bảng tổng hợp sinh ra từ `aggregate.py` |

Chạy lại tổng hợp: `python src/evaluation/results/role_permission_eval/aggregate.py`

## Phân bố outcome

| outcome | bo | me | con_lon | con_nho |
|---|--:|--:|--:|--:|
| executed | 59 | 58 | 32 | 39 |
| approval | · | · | 23 | · |
| blocked | · | · | 6 | 24 |
| noop | 11 | 13 | 9 | 6 |
| clarification | 17 | 16 | 18 | 18 |
| answered | 9 | 9 | 9 | 9 |
| no_goal | 4 | 4 | 3 | 4 |
| abstain / error | · | · | · | · |

Latency: bố median 3855ms / p95 16.3s · mẹ 3637 / 15.3s · con_lon 3118 / 16.8s · con_nho 3033 / 13.0s.

## Kết luận — phân quyền theo vai trò hoạt động đúng

- **Bố & mẹ (OWNER/adult):** an ninh và công-suất-lớn **thực thi ngay**, không cần duyệt —
  đúng invariant "cha mẹ quyền cao nhất".
- **Con lớn (teen):** công-suất-lớn (điều hoà/sưởi/bình nóng/máy rửa bát) → **approval (HITL)**;
  an ninh (khoá cửa/camera) → **blocked**. Gia dụng thường (máy lọc, robot hút bụi) → **executed**.
- **Con nhỏ (child):** cả công-suất-lớn lẫn an ninh → **blocked**; chỉ còn gia dụng an toàn & đèn.
- Cột `clarification`/`answered`/`out_of_scope` gần như **đồng nhất giữa các role** → khác biệt đến
  từ **tầng chấm quyền**, không phải NLU hiểu khác nhau. Hiểu ngôn ngữ ổn định; quyền phân tầng đúng.
