# Worklog — VinButler

> Lịch sử phát triển theo **ngày**, tổng hợp tự động từ `git log` (mọi nhánh, không tính merge).
> Khoảng: **2026-07-23 → 2026-08-31** · **253 commit** · nhóm: Dũng, Đại, Phái, Sơn.

## Tổng hợp đóng góp

| Thành viên | Commit (non-merge) | Số ngày active |
|---|---:|---:|
| **Dũng** | 156 | 29 |
| **Đại** | 62 | 17 |
| **Phái** | 18 | 13 |
| **Sơn** | 17 | 11 |
| **Tổng** | **253** | — |

---

## Nhật ký theo ngày

### 2026-08-31 — 6 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Đại** | chore(docs): gom tai lieu ban giao vao docs/handoff + xoa artifact ragas json · docs(deliverables): sap xep deliverables ve dung vi tri + don file loi thoi |
| **Dũng** | test CI · sửa phần thông báo và thêm docs pitch · đổi seft host · sửa fe 1 tí |

### 2026-08-30 — 24 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | merge be với fe giao diện mới · Số trong câu không mặc nhiên là setpoint của lượt trước · fix bug long conversation |
| **Dũng** | sửa thông báo lặp · sửa output chatbox · xong responsive · responsive v1 · tích hợp FE mới · sửa trang điện · xong phần trang lịch sử · ok phần điện · xong thông báo · xong phần thông báo · sửa phần thông báo · sửa nút cảm biến · sửa về giao diện thành viên · 1 · thêm các tính năng chưa có · sửa xong pop up trên map · ok hơn · vẫn như cứt · thêm các trang · xong trang đầu · kết nối với giao diện mới |

### 2026-08-28 — 1 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Đại** | fix(suggestion): áp dụng trước rồi mới gỡ đề xuất + test dedup thông báo V3 |

### 2026-08-27 — 7 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | sửa thông báo, lịch sử · sync UI với backend |
| **Đại** | build(typecheck): dung mypy gate + tra het no typing (0 loi) · docs(handoff): tai lieu ban giao FE cho cong suat + so dien (do thi) · feat(energy): endpoint chuoi kWh theo ngay/thang cho do thi (OWNER-only) · feat(fe): them API client energyUsage() goi GET /energy/usage · feat(energy): so dien tich luy (kWh) hom nay + thang nay cho toan nha |

### 2026-08-26 — 7 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | sửa nút cảm biến · xong phần responsive, chưa test · tạm ổn responsive · sửa sidebar chatbox |
| **Đại** | feat(members): tao thanh vien moi tu cap quyen phong rieng · feat(seed): cap san quyen phong rieng cho thanh vien khi seed · feat(permissions): them grant_private_room_access cap san quyen phong rieng |

### 2026-08-25 — 11 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | Replace develop_Phaihoang with current local code |
| **Dũng** | sửa vị trí file sang test · sửa xong camera · sửa camera · thêm API về cảm biến real time · chỉnh sửa cam, đúng với quyền, với thiết bị hiện có · xong map phóng to · sửa được map phóng to nhưng thiếu phần cảm biến · sửa xong ô output · xoá sở thích và chỉnh ô output · Merge remote-tracking branch 'origin/kiem_thu' into develop_TrongDung |

### 2026-08-24 — 21 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Đại** | fix(db): default boolean dung TRUE/FALSE thay vi 1/0 cho Postgres · test(concurrency): dispose engine truoc khi xoa temp DB (fix flake Windows) · fix(backend): sua dung 5 loi B1-B5, undo xoa preference khoi BE · first using codex · fix(backend): dong bo profile va go preference tinh · refactor(iot): cắt gọn capability thiết bị cho khớp FE (bật/tắt + cường độ) |
| **Sơn** | p |
| **Dũng** | fix bug nhập 2 lần · thêm docs so sánh với be · cập nhật phần thiết bị chỉ có bật tắt, cường độ · xoá máy suỏi khỏi hệ thống · đổi sang lọc thông báo thay vì xoá · fix pop up chỉnh sửa quyền · sửa được phần quản lý · intial fix · sửa tên · sửa tích hợp · test UI · fix chức năng |
| **Phái** |  xoá preference · done phan kiem thu |

### 2026-08-23 — 3 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Đại** | feat(conflict): hybrid override-rồi-báo cho xung đột giữa người-với-người · feat(conflict): override HITL + auto-resolve thay cho chặn cứng |
| **Dũng** | sửa environment tool fail-safe + cập nhật báo cáo bàn giao |

### 2026-08-22 — 27 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | humanize text · theo a đại · sửa bug thông báo · sửa phần trang quản lý và hồ sơ cá nhân · đã tích hợp backend mới · trở về bản cũ backend · sửa thêm thói quen · sửa FE · sửa FE · hoàn thiện phần docs thống kê tính năng |
| **Phái** |  thêm guardrail nhẹ |
| **Đại** | fix(notifications): gửi kèm id trong WS để FE thêm được vào dropdown · docs: bàn giao trọn gói — bảng WS events + notification types + payload; đánh dấu doc cũ · docs: cập nhật bàn giao — hoàn thành nốt migration/conflict-in-control/habit-unify/notification · refactor(habit): Mục 7 — dùng resolve_priority chung cho xung đột chéo người · feat(migrate): Mục 2 — versioned migration cho DATA (AccessEffect cũ → mới) · feat(control): Mục 6 — conflict check khi điều khiển từ map + notification conflict_warning · feat(notifications): Mục 8 — thêm approval_requested + child_lock_changed persistent · docs: tài liệu bàn giao triển khai PLAN-BE (childlock + bỏ age) · feat(conflict): Mục 7 — xung đột HABIT_SCHEDULE + ngữ cảnh thói quen (phần lõi) · feat(notifications): Mục 8 — mở rộng loại thông báo + báo người yêu cầu · feat(members): Mục 4 — GET /members/me/access (thành viên tự xem quyền) · feat(approval): Mục 6 — hết hạn + idempotent + recheck khi duyệt · feat(childlock): Mục 5 — API khoá trẻ em + WS broadcast · feat(access): Mục 2+3 — bỏ age, nền model mới + childlock trong resolve_access |
| **Sơn** | sửa lỗi map to ấn vào các nút không phản hồi, hiển thị · thêm cảnh báo, animation, ngưỡng, sửa lỗi demo, nâng cấp thông báo |

### 2026-08-21 — 5 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | sửa lỗi logic khi nối luông agent v2 với ui mới |
| **Đại** | feat(history): live logs realtime — đẩy WebSocket + chèn dòng mượt (B2) |
| **Dũng** | ok sửa thêm phần camera · sửa trang lịch sử · đơn giản hoá màu map |

### 2026-08-20 — 13 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | Hoàn thành kiến trúc V2 multi-agent |
| **Dũng** | sửa giao diện map · sửa map · sửa biểu mẫu thêm thành viên · sửa FE · sửa phần BE/AI |
| **Đại** | docs: cập nhật ARCHITECTURE + tài liệu bàn giao phân quyền (Phase 8) · feat(fe): đồng bộ giao diện phân quyền sang 3 trạng thái (Phase 6) · feat(agent): lệnh NL qua cùng cổng resolve_access + migration (Phase 7) · feat(access): cấp quyền cả phòng bằng thao tác hàng loạt (Phase 5) · feat(access): xác nhận-lúc-cấp cho thiết bị an ninh (Phase 4) · feat(notifications): kênh thông báo cho chủ hộ (Phase 3 — trạng thái ALERT) · feat(permissions): quản quyền theo thiết bị + 3 trạng thái accepted/alert/request |

### 2026-08-19 — 9 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | chốt bản sáng · xong bản sáng · sửa giao diện nền sáng · thêm video live demo mới · fix đưộcw nút thêm thành viên · vẫn chưa được gói thêm thành viên |
| **Sơn** | noti fb · merge main + color + swap chat |
| **Đại** | deploy: fix quyền non-root trong Dockerfile + bỏ KE_HOACH_DEPLOY.md |

### 2026-08-18 — 9 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | sửa FE · chỉnh mockFE thành FE · sửa FE · thêm nút đăng nhập · tích hợp với BE mockFE |
| **Đại** | deploy: tự chuẩn hoá DATABASE_URL sang psycopg + cập nhật kế hoạch deploy · update KE_HOACH_DEPLOY.MD · deploy: chuẩn bị triển khai Render (giữ nguyên chạy local) |
| **Sơn** | Create user_research.docx |

### 2026-08-17 — 18 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | hoàn thiện hơn · thêm nút cmar biến · thêm bảng demo · chốt · chốt nhưng để chỉnh thêm cho rooms · ok · tạm ổn · tạm ổn · sửa 1 · làm ô rooms · xong, chuẩn bị sang room · sửa 1 · hoàn thiện trang hisory và trang member · thêm trang history · hoàn thành trang main của mockFE · sửa 1 · làm mockFE mới · sửa thói quen |

### 2026-08-16 — 2 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | đọc để test thói quen trên FE · thêm được tính năng thói quen mới |

### 2026-08-15 — 6 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | fix(automation): dedup thói quen theo TỪNG người, không chặn cả hộ (#6) · fix(devices): duyệt HITL xong phải THỰC THI thật + đồng bộ realtime · feat(fe): gộp popup thói quen theo khung giờ, kèm tên thiết bị + mức độ · fix(seed): đóng dấu lịch sử demo theo giờ VN + action hợp lệ, kèm mức độ · fix(memory): episodic + retrieval bám đồng hồ mô phỏng theo giờ VN (Nhóm 1&4) · 1 và 3 |

### 2026-08-14 — 13 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | Bản vá hiểu từ ngữ mơ hồ lần thứ N |
| **Đại** | fix(role2): vá Nhóm 3 (an ninh /clock), Nhóm 5 (schema/index), Nhóm 2 (đề xuất đúng người) · feat(clock): đồng hồ mô phỏng mirror FE + endpoint /clock, log ăn theo giờ FE · feat(db): nạp lịch sử hành động từ JSON template cho demo thói quen · feat(habits): thực thi thói quen qua phân quyền + API quản lý thói quen · feat(habits): quy đúng người khi thực thi thói quen + mô phỏng lịch sử 4 người |
| **Dũng** | fix(habits): chặn vòng tự khuếch đại + DELETE không còn bị tái tạo · fix cố định icon sidebar · tiếpn tục · sửa lại cho mock_FE thành FE |
| **Sơn** | demo · update thời gian · tab setting |

### 2026-08-13 — 5 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | chore(config): bật lại khoá model về gpt-4o-mini · fix(nlu): 3 lỗi sai âm thầm ở pipeline + bộ công cụ fine-tune model local · thêm responsive cho FE · Sửa phần sidebar |
| **Phái** | Bản vá hiểu từ ngữ mơ hồ |

### 2026-08-12 — 5 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | update agent · Bản vá agent |
| **Dũng** | xoá trang thiết bị · sửa đèn · sửa về AI và chạy được |

### 2026-08-11 — 6 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | sửa tên thành G2 · sửa UI bị lỗi ở phòng bếp và điều hoà |
| **Phái** | feat(dialogue): ký ức hội thoại multi-turn (anaphora + carry ngữ cảnh) · Sửa lỗi phần tắt thiết bị |
| **Sơn** | Gate 2 |
| **Đại** | feat(rooms): quản lý phòng + thêm/sửa/xoá thiết bị động (1A + 2A) Phòng thành entity thật và thiết bị tạo được lúc chạy từ danh mục loại cố định. - models: bảng Room + Device.room_id (tạo được phòng rỗng; xoá phòng -> thiết bị   chưa gán, không xoá theo) - registry: spec_from_device() dựng spec từ bản ghi DB, TEMPLATE_BY_TYPE làm khuôn   theo loại cho form thêm thiết bị - memory_bus: điều khiển theo device DB thay vì spec_for(slug) tĩnh -> thiết bị   người dùng tự thêm vẫn điều khiển được - api/rooms.py: CRUD phòng (xem: mọi thành viên; sửa: chủ hộ), đổi tên đồng bộ   xuống thiết bị - api/devices.py: GET /device-catalog + POST/PATCH/DELETE /devices (chủ hộ) - seed: tạo phòng mặc định + gắn room_id, backfill cho DB cũ |

### 2026-08-10 — 3 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | cải thiện suy luân hiểu các từ ngữ mơ hồ |
| **Dũng** | fix(ci): sửa lint ruff và gỡ job frontend đã lỗi thời · sửa UI, thêm về AI |

### 2026-08-09 — 1 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | initial: đã hiểu được phần nào từ ngữ mơ hồ |

### 2026-08-08 — 1 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | sửa tổng thể UI, đồng bộ lại thông tin |

### 2026-08-07 — 4 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | chạy được với BE cũ · push · chi tiết UI hơnn |
| **Đại** | feat(iot): mở rộng thiết bị nhiều trạng thái khớp giao diện mock_FE Thêm các capability và action cho thiết bị vượt ngoài bật/tắt, để backend đỡ được giao diện điều khiển phong phú bên mock_FE. - enums: 11 capability mới (color_temp, color, hvac_mode, operation_mode,   away_mode, media_control, media_source, preset_mode, oscillate,   vacuum_control, program) - simulator: 20 action mới kèm handler + validate giá trị lựa chọn   (chế độ HVAC, nguồn media, preset quạt, chương trình máy rửa bát...) - registry: _enrich() tự gắn capability theo loại thiết bị (đèn→màu,   điều hoà→HVAC, media→phát/nguồn, robot→điều khiển hút bụi...) Chỉ thêm capability, không đổi initial_state nên không phá test hiện có. Thiết bị không có capability tương ứng vẫn bị chặn đúng như cũ. |

### 2026-08-06 — 17 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | sửa lỗi side bar · sửa tiếp · sửa ảnh mặt bằng · sửa xong các nút · sửa khung phòng khách · xong thêm ai agent · thêm khung ai agent · xong sidebar · chỉnh sidebar · xong sidebar và topbar · sửa7 · sửa6 · sưa4 · sửa3 · sửa2 · sửa_1 · mock FE của HA |

### 2026-08-05 — 12 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | FE v1 · FE · config ô mặt bằng |
| **Sơn** | sửa UI |
| **Đại** | feat(hitl): realtime popup cho chủ hộ khi member vượt quyền (bước 3/3) · feat(hitl): realtime popup cho chủ hộ khi member vượt quyền (bước 3/3) · feat(members): thêm/sửa thành viên + đặt quyền truy cập thiết bị (bước 2/3) · feat(members): thêm/sửa thành viên + đặt quyền truy cập thiết bị (bước 2/3) · feat(access): nền tảng phân quyền per-user × per-device (bước 1/3) · feat(access): nền tảng phân quyền per-user × per-device (bước 1/3) · fix: trả timestamp API kèm UTC để client hiển thị đúng múi giờ · fix: trả timestamp API kèm UTC để client hiển thị đúng múi giờ |

### 2026-08-04 — 8 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | thêm ô floor plan trong FE · xoá cái không cần thiết · hoàn thiện mock ui · mock đã chia 1/4 |
| **Sơn** | push · delete mock · UI son |
| **Đại** | backend: checkpointer bền vững, refresh token, phân trang, heartbeat, máy sưởi, cấu hình deploy |

### 2026-08-03 — 2 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | thêm mock FE |
| **Sơn** | doc |

### 2026-08-02 — 1 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | thêm G1 vào |

### 2026-07-29 — 1 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Sơn** | day 4 |

### 2026-07-28 — 1 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Sơn** | start coding |

### 2026-07-26 — 3 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Phái** | chore: setup ai logging |
| **Đại** | dai first commit |
| **Dũng** | adding instruction |

### 2026-07-25 — 1 commit

| Thành viên | Công việc (commit) |
|---|---|
| **Dũng** | TrongDung first commit |

