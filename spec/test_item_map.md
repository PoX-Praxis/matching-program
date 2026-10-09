# 受入項目 → 自動テスト 対応表（指示書48 G-3）

更新: 2026-09-27（45C 時点）
対象: `PoX_テスト項目一覧.md` の **実装側 66 項目**（1〜19, 26, 31〜37, 39〜44, 46〜50, 63, 68〜75, 76〜85, 88〜90, 93〜98）と、実機テスト由来の項目（99〜117）。

## 区分の凡例

| 区分 | 意味 |
|---|---|
| **新規23**（46） | 指示書46 §4（PR #105）で `tests/test_acceptance_items.py` に追加した 23 件。うち **16 件が 66 項目**（19 項目分）、**7 件が実機テスト1 由来**（99, 101, 107, 108, 108-a〜c） |
| **48** | 指示書48 で追加（PR #107・#108） |
| **49** | 指示書49 で追加 |
| **45A** | 指示書45 A群で追加 |
| **45B** | 指示書45B（`redaction.recorded`）で追加 |
| **45C** | 指示書45C（境界・削除手順・審議の引き継ぎ）で追加 |
| **45A 追補2** | 見送り後の再申請で追加（再申請は可・発注者の決定 2026-09-27） |
| **既存** | 指示書41〜44 の実装時からあるテスト |
| **未実装** | 実装が無いのでテストも無い（理由を記載） |

テストファイル略記: `acc`=test_acceptance_items, `rights`=test_action_rights, `part`=test_project_participation, `gov`=test_governance_ledger, `flow`=test_talks_flow, `clo`=test_talk_closure, `hier`=test_talk_hierarchy, `http`=test_intent_flow_http, `mem`=test_member_ledger, `agr`=test_agreement, `vis`=test_community_visibility, `intake`=test_community_intake, `dorm`=test_dormancy, `hyg`=test_ledger_hygiene, `red`=test_redaction, `traj`=test_trajectory, `leave`=test_leave

## 1. 台帳・合意

| 項目 | テスト | 区分 |
|---|---|---|
| 1 | `acc::test_t001_subject_created_on_community` | 新規23 |
| 2 | `mem::test_create_emits_founder_joined`（ジェネシス member.joined のフォールバック読み）。**遡及書き換えが無いことの直接テストは無い**（部分） | 既存 |
| 3 | `mem::test_request_join_not_in_ledger_then_approve_emits`, `mem::test_leave_emits_member_left_and_derivation` | 既存 |
| 4, 5 | `acc::test_t004_t005_purpose_and_launched_fields`, `gov::test_purpose_agreed_payload_shape` | 新規23＋既存 |
| 6 | `gov::test_intent_launched_and_participant_and_completed_payloads`, `gov::test_participant_kind_validated` | 既存 |
| 7, 10 | `gov::test_intent_launched_and_participant_and_completed_payloads` | 既存 |
| 8, 9 | `acc::test_t008_t009_old_intent_events_not_written` | 新規23 |
| 11 | `acc::test_t011_t080_no_ledger_update_or_delete_routes` | 48 |
| 12 | `acc::test_t012_decision_ignores_later_members`, `agr::test_later_joiners_do_not_change_outcome`, `gov::test_later_members_do_not_change_recomputation` | 新規23＋既存 |
| 13 | `acc::test_t013_decision_ignores_current_time` | 新規23 |
| 14 | `acc::test_t014_ruleset_version_frozen_at_talk_creation` | 48 |

## 2. トークの階層と一覧

| 項目 | テスト | 区分 |
|---|---|---|
| 15 | `flow::test_proposal_talk_agrees_and_writes_purpose` | 既存 |
| 16 | `flow::test_public_talks_named_to_third_party` | 既存 |
| 17 | `flow::test_proposal_talk_agrees_and_writes_purpose` ＋ `hier::test_project_origin_and_execution_talk` | 既存 |
| 18, 19 | `hier::test_project_origin_and_execution_talk` | 既存 |
| 26 | `rights::test_t108o_only_participant_can_post_to_project`（実行トークへの投稿が同じ posts で動く） | 48 |

## 3. 状態と閉鎖

| 項目 | テスト | 区分 |
|---|---|---|
| 31 | `acc::test_t031_agreed_proposal_post_returns_409`, `clo::test_closed_proposal_rejects_post_and_vote` | 新規23＋既存 |
| 32 | `acc::test_t032_completed_project_rejects_post_409` | 48 |
| 33 | `acc::test_t033_dormant_proposal_allows_post` | 新規23→49 で修正 |
| 34 | `clo::test_open_proposal_allows_post_and_vote` | 既存 |
| 35 | 35-a〜35-d に分割（下記） | 49 |
| 35-a | `dorm::test_t035a_dormant_after_threshold` | 49 |
| 35-b | `dorm::test_t035b_dormancy_writes_no_ledger_event` | 49 |
| 35-c | `dorm::test_t035c_dormant_talk_stays_public` | 49 |
| 35-d | `dorm::test_t035d_no_dormancy_on_admission_join_complete` | 45C |
| 36 | 36-a・36-b に分割（下記） | 49 |
| 36-a | `dorm::test_t036a_post_revives_dormant_talk` | 49 |
| 36-b | `dorm::test_t036b_dormant_talk_accepts_post_and_vote` | 49 |
| （対象外） | `dorm::test_t049_project_and_chat_never_dormant` | 49 追補 |
| （票の変更） | `dorm::test_t049_vote_change_counts_as_activity` | 45C |
| （決定性） | `dorm::test_t049_agreement_ignores_clock` | 49 |

**`test_t033` の扱い（49 追補 §2）**: 指示書46 で入った旧 `test_t033_dormant_open_proposal_allows_post` は、名前に反して**審議中の提議への投稿しか確かめておらず、休眠を検証していなかった**（カバレッジの錯覚）。指示書49 で休眠そのものの検証を 35-a〜36-b として追加し、`test_t033` は「時刻を注入して実際に休眠にした提議へ投稿できる」ことを確かめる形に書き直した（36-b と同じ性質の確認として残している）。
| 37 | `acc::test_t037_closed_talks_are_not_deleted`, `acc::test_t099_completed_project_stays_in_list` | 48＋新規23 |

## 4. 可視性・プライバシー

| 項目 | テスト | 区分 |
|---|---|---|
| 39 | `flow::test_public_talks_named_to_third_party` | 既存 |
| 40 | `acc::test_t040_public_talk_content_same_for_all_viewers`（内容は全員同一。閲覧者で変わるのは行為の導線 4 項目のみ） | 48 |
| 41 | `acc::test_t041_admission_talk_404_to_third_party`, `clo::test_admission_talk_members_only` | 新規23＋既存 |
| 42 | `acc::test_t042_admission_talk_visible_to_members` | 48 |
| 43 | `acc::test_t043_chat_404_to_third_party`, `flow::test_chat_visible_only_to_members` | 新規23＋既存 |
| 44 | `acc::test_t044_pending_hidden_from_third_party`, `vis::test_pending_hidden_from_third_party` ほか | 新規23＋既存 |
| 46, 47 | `acc::test_t046_t047_no_talks_by_participant_or_search_route` | 48 |
| 48, 50 | `acc::test_t048_t050_no_raw_id_in_talk_view`, `rights::test_t114_no_raw_id_for_unnamed_accounts` | 新規23＋48 |
| 49 | `rights::test_t114_no_raw_id_for_unnamed_accounts`（表示名未設定は「表示名未設定のアカウント」） | 48 |

## 6. ダイアログと文言

| 項目 | テスト | 区分 |
|---|---|---|
| 63 | `acc::test_t063_no_window_prompt_in_templates` | 新規23 |

## 7. 下流（完了・実績）

| 項目 | テスト | 区分 |
|---|---|---|
| 68 | `gov::test_completed_downstream_reads_new_flow` | 既存 |
| 69 | `intake::test_completed_episodes_deterministic_rule`, `intake::test_mapper_detects_both_types`（部分: 選定規則のみ。生成〜合意〜実績表示の接続は指示書49） | 既存 |
| 70 | `gov::test_distribution_inputs_derivable_from_ledger`（部分） | 既存 |
| 71 | `http::test_old_flow_via_ledger_functions_still_reads` | 既存 |
| 72 | `http::test_old_intent_write_endpoints_are_frozen`, `acc::test_t080_old_intent_write_endpoints_frozen` | 既存＋新規23 |

## 8. 決定性・再現性

| 項目 | テスト | 区分 |
|---|---|---|
| 73 | `gov::test_agreement_recomputable_from_ledger_only` | 既存 |
| 74 | `acc::test_t074_basis_seq_unchanged_after_later_events` | 48 |
| 75 | `acc::test_t075_agreement_is_irreversible` | 48 |

## 9. 異常系・API 直叩き

| 項目 | テスト | 区分 |
|---|---|---|
| 76 | `acc::test_t076_direct_post_to_closed_409` | 新規23 |
| 77 | `acc::test_t077_admission_direct_404_unauth` | 新規23 |
| 78 | `acc::test_t043_chat_404_to_third_party` | 新規23 |
| 79 | `acc::test_t048_t050_no_raw_id_in_talk_view`, `rights::test_t114_no_raw_id_for_unnamed_accounts`（部分: 全エンドポイントの探索ではない。プロフィール・DM は表示名＋id 併記が仕様） | 新規23＋48 |
| 80 | `acc::test_t080_old_intent_write_endpoints_frozen`, `acc::test_t011_t080_no_ledger_update_or_delete_routes` | 新規23＋48 |

## 10. 指示書45

| 項目 | テスト | 区分 |
|---|---|---|
| 81 | `hyg::test_t081_admission_closed_after_decision`, `hyg::test_t081_dormant_admission_is_not_rejected`, `hyg::test_t081_decline_requires_dissent_and_membership` | 45A |
| 82 | `hyg::test_t082_decline_not_written_to_ledger` | 45A |
| 83 | `hyg::test_t083_admission_approval_writes_member_joined` | 45A |
| 84 | `hyg::test_t084_applicant_sees_only_own_outcome` | 45A |
| 85 | `hyg::test_t085_noindex_on_member_only_surfaces`, `hyg::test_t085_robots_txt_exists` | 45A |
| 86, 87 | 運用（キャッシュ・検索インデックス）。自動テストの対象外 | 運用 |
| 88 | `hyg::test_t088_approvals_work_without_keys` | 45A |
| 89, 90 | `hyg::test_t089_t090_documented`（`docs/ledger_limits.md`） | 45A |
| 91 | 確認報告（離脱の実装有無）。テストは既存 `mem::test_leave_emits_member_left_and_derivation` | 既存 |
| 92 | 運用（デプロイ版の同一性）。自動テストの対象外 | 運用 |
| 93 | `red::test_t093_recompute_matches_result_hash`, `red::test_t093_same_timestamp_posts_use_insertion_order` | 45B |
| 93-a | `red::test_t093a_legacy_agreement_not_flagged` | 45B→45C で境界を seq に |
| 93-b | `red::test_t093b_constant_placeholder_reproduces_result_hash` | 45C |
| 93-c | `red::test_t093c_boundary_unset_fails_fast` | 45C |
| 93-d（監査） | `red::test_t045c_audit_lists_mismatches_after_boundary` | 45C |
| （伏せ字の投稿禁止） | `red::test_t093d_placeholder_cannot_be_posted` | 45C |
| 94 | `red::test_t094_mismatch_is_detected_as_tampering` | 45B |
| 95 | `red::test_t095_redactions_chain` | 45B |
| 96 | `red::test_t096_redacted_text_not_in_ledger` | 45B |
| 97 | `red::test_t097_complainant_not_in_ledger` | 45B |
| 98 | `red::test_t098_removing_redaction_record_breaks_verification` | 45B |
| 118 | `hyg::test_t118_declined_applicant_can_reapply` | 45A 追補2 |
| 119 | `hyg::test_t119_self_view_shows_decline_and_reapply` | 45A 追補2 |
| 119-a | `hyg::test_t119a_reapply_starts_fresh_review` | 45C |
| 119-b | `hyg::test_t119b_no_reapply_affordance_for_member_or_pending` | 45C |
| 120 | `hyg::test_t120_reapply_is_normal_review_and_duplicate_409` | 45A 追補2 |
| 121 | `hyg::test_t121_decline_and_reapply_not_in_ledger` | 45A 追補2 |

## 12. 実機テスト由来（99〜117）

| 項目 | テスト | 区分 |
|---|---|---|
| 99 | `acc::test_t099_completed_project_stays_in_list` | 新規23 |
| 100 | `rights::test_g2_no_double_marker_and_achievement_empty_reason` | 48 |
| 101 | `acc::test_t101_button_terminology_no_mix` | 新規23 |
| 102 | `rights::test_g1_agree_on_proposal_detail` | 48 |
| 107 | `acc::test_t107_launch_members_only` | 新規23 |
| 108 | `acc::test_t108_external_individual_joins_project` | 新規23 |
| 108-a | `acc::test_t108a_community_joins_with_consent_ref`, `part::test_t108a_community_join_options_and_consent` | 新規23＋47 |
| 108-b | `acc::test_t108b_participation_denominator_is_participants_headcount`, `rights::test_t108b_denominator_is_basis_not_current_participants` | 新規23＋48 |
| 108-c | `acc::test_t108c_project_join_is_public`, `part::test_t108c_join_talk_is_public` | 新規23＋47 |
| 108-d〜j | `part::test_t108d_…` 〜 `part::test_t108j_…`（108-g は 108-r に合わせて訂正） | 47 |
| 108-k | `rights::test_t108k_launcher_gets_no_join_offer` | 48 |
| 108-l | `rights::test_t108l_participants_and_denominator_always_shown` | 48 |
| 108-m | `rights::test_t108m_no_community_join_on_own_project` | 48 |
| 108-n | `rights::test_t108n_unaffiliated_individual_note` | 48 |
| 108-o | `rights::test_t108o_only_participant_can_post_to_project` | 48 |
| 108-p | `rights::test_t108p_owning_member_can_offer_as_individual` | 48 |
| 108-r | `rights::test_t108r_offerer_not_in_denominator` | 48 |
| 108-s | `rights::test_t108s_participant_list_on_project_detail_after_join` | 48 |
| 109, 110 | `rights::test_g2_no_double_marker_and_achievement_empty_reason` | 48 |
| 111 | `rights::test_t111_participant_can_propose_complete`, `rights::test_t111_non_participant_has_no_complete_action` | 48 |
| 112 | `rights::test_t112_guest_no_join_offer_and_api_401` | 48 |
| 113 | `rights::test_t113_guest_post_to_proposal_401` | 48 |
| 113-a | `rights::test_t113a_nonmember_post_to_proposal_403` | 48 |
| 114 | `rights::test_t114_no_raw_id_for_unnamed_accounts` | 48 |
| 115 | `rights::test_t115_no_post_box_for_non_participant` | 48 |
| 116 | `rights::test_t116_guest_post_401` | 48 |
| 117 | `rights::test_t117_member_not_participant_cannot_post_403` | 48 |

103〜106 は人の確認項目（103: 収束案と票の表示、104: 出自リンク、105: 締め切りバナー、106: 名前の変更不可）で、自動テストの対象外。

## 軌跡（指示書50 v3・122〜144）

| 項目 | テスト |
|---|---|
| 122 | `traj::test_t122_version_tree` |
| 123 | `traj::test_t123_third_party_sees_text` |
| 124・125 | `traj::test_t124_t125_evidence_numbers_raw_owner_only` |
| 126 | `traj::test_t126_connection_shows_both_versions` |
| 127・128 | `traj::test_t127_t128_hidden_version` |
| 129 | `traj::test_t129_setbacks_not_on_trajectory` |
| 129-a | `traj::test_t129a_pending_admission_not_a_branch` |
| 130 | `traj::test_t130_no_ledger_events` |
| 131 | `traj::test_t131_append_only_and_visibility_history` |
| 132 | `traj::test_t132_decoration_does_not_create_version_and_gaps_visible` |
| 133 | `traj::test_t133_current_version_shown_above` |
| 134 | `traj::test_t134_anchor_page_noindex` |
| 135 | `traj::test_t135_no_cross_listing_routes` |
| 136 | `traj::test_t136_connection_other_display_name` |
| 137 | `traj::test_t137_causality_note` |
| 138 | `traj::test_t138_hidden_default_off` |
| 139 | `traj::test_t139_all_kinds_present` |
| 140・141 | `traj::test_t140_t141_no_magnitude_styling_or_time_scale` |
| 142 | `traj::test_t142_partially_redacted_marker` |
| 143 | `traj::test_t143_only_standing_necessity` |
| 144 | `traj::test_t144_view_overrides_no_version` |

## 離脱（指示書51・145〜156）

| 項目 | テスト |
|---|---|
| 145 | `leave::test_t145_community_leave_writes_member_left` |
| 146 | `leave::test_t146_project_leave_writes_event_with_joined_ref` |
| 147 | `leave::test_t147_left_participant_not_in_later_denominator` |
| 148 | `leave::test_t148_past_denominator_not_retroactive` |
| 149・150 | `leave::test_t149_t150_posts_remain_and_removed_from_list` |
| 151 | `leave::test_t151_after_leave_post_forbidden` |
| 152 | `leave::test_t152_double_leave_409` |
| 153 | `leave::test_t153_no_expulsion` |
| 154 | `leave::test_t154_rights_remain_after_leave` |
| 155 | `leave::test_t155_only_one_new_event_type` |
| 156 | `leave::test_t156_visibility_unchanged` |
| (a) | `leave::test_t051a_launcher_can_leave_but_not_last_one` |
| (c) | `leave::test_t051c_founder_bootstrap_cannot_leave`, `leave::test_t051c_founder_rights_end_but_fact_remains`, `leave::test_t051c_community_edit_requires_session_founder` |
| §8-1 | `leave::test_t051_reoffer_after_leave_allowed` |
| 画面 | `leave::test_t051_leave_affordance_rendered` |

## つながるの再設計（指示書55・55-2・161〜194）

| 項目 | テスト |
|---|---|
| 161 | `match_auth::test_t161_v4_match_self_only`, `match_auth::test_t161_legacy_match_self_only`, `match_auth::test_t161_necessity_id_of_others_is_forbidden` |
| 162 | `match_auth::test_t162_no_numbers_in_response`, `match_auth::test_t162_legacy_match_returns_ids_only` |
| 163・164 | `connect_page::test_connect_has_no_sort_or_score`, `match_auth::test_t165_ui_no_longer_uses_limiting_axis_or_score` |
| 165 | `match_auth::test_t165_effective_axis_is_strongest_not_limiting` |
| 166 | `t55b_connect::test_t166_direction_b_is_computed`, `t55b_connect::test_t166_necessity_path_passes_owner_state` |
| 167 | `t55b_connect::test_t167_candidates_without_necessity_are_excluded`, `t55b_connect::test_t167_seeker_without_necessity_gets_no_necessity_state` |
| 168 | `t55b_connect::test_t168_directory_reads_v4_not_v3`, `t55b_connect::test_t168_connect_flow_does_not_use_v3_match` |
| 169・171 | `t55b_connect::test_t169_t171_directory_no_status_no_truncation` |
| 170 | `t55b_connect::test_t170_no_counts` |
| 172 | `t55b_connect::test_t172_templates_do_not_render_raw_ids`, `t55b_connect::test_t172_api_names_fall_back_to_label_not_id` |
| 183 | `t55b_connect::test_t183_prod_refuses_stub` |
| 184 | `t55b_connect::test_t184_stub_shows_no_results_but_other_screens_work`, `t55b_connect::test_t184_matching_available_follows_backend` |
| 186 | `t55b_connect::test_t186_engaged_are_excluded_from_results_not_directory` |
| 187 | `t55b_connect::test_t187_axes_are_summarized`, `t55b_connect::test_t187_public_axis_values` |
| 190 | `t55b_connect::test_t190_new_registrant_enters_pool_with_same_tag` |
| 191 | `t55b_connect::test_t191_stub_tag_is_separated_from_real_tags` |
| 192 | `t55b_connect::test_t192_startup_log_reports_backend_and_tag` |
| 193 | `match_auth::test_t193_no_unexpected_unauthenticated_write_routes` |
| 194 | `match_auth::test_t194_t161_profile_edit_self_only` |
| 178 | `t55d_handles::test_t178_label_is_name_with_handle`, `t55d_handles::test_t178_api_profile_carries_handle` |
| 179 | `t55d_handles::test_t179_unique_and_immutable`, `t55d_handles::test_t179_api_first_set_then_409`, `t55d_handles::test_t179_handle_cannot_be_another_subject_id`, `t55d_handles::test_t179_confirm_requires_handle_for_new_users`, `t55d_handles::test_t179_draft_preview_suggests_handle_from_seeker_id` |
| 188 | `t55d_handles::test_t188_profile_url_uses_handle`, `t55d_handles::test_t188_without_handle_stays_on_old_url` |
| 189 | `t55d_handles::test_t189_exception_change_once_and_old_retired`, `t55d_handles::test_t189_change_is_recorded_not_shown` |
| 173・182 | `t55c_connections::test_t173_t182_withdraw_removes_request_and_message`, `t55c_connections::test_t173_cannot_withdraw_others_request` |
| 174 | `t55c_connections::test_t174_double_offer_is_idempotent_and_shows_pending` |
| 177 | `t55c_connections::test_t177_dm_requires_connection` |
| 180 | `t55c_connections::test_t180_offer_message_optional_and_limited`, `t55c_connections::test_t180_offer_message_stays_at_top_of_conversation_and_can_be_retracted` |
| 181 | `t55c_connections::test_t181_only_recipient_reads_offer_message` |
| 185 | `t55e_audit_match::test_t185_no_matching_while_someone_lacks_current_tag`, `t55e_audit_match::test_t185_old_tag_rows_alone_do_not_stop_matching`, `t55e_audit_match::test_t185_postgres_query_counts_vectorized_only` |
| 55-4 §2 | `t55e_audit_match::test_audit_match_requires_token_and_pair`, `t55e_audit_match::test_audit_match_returns_both_directions_without_text`, `t55e_audit_match::test_audit_match_writes_nothing` |
| 55-3 §3 | `t55e_audit_match::test_necessity_vectors_carry_model_tag`, `t55e_audit_match::test_allow_stub_is_refused_on_render` |
| 55-5 §1・§2 | `t55f_approval_context::test_reason_for_incoming_offer`, `t55f_approval_context::test_reason_only_for_engaged_pairs`, `t55f_approval_context::test_reason_absent_when_matching_unavailable`, `t55f_approval_context::test_approval_screens_show_reason_and_offer_message` |
| 55-5 §3 | `t55f_approval_context::test_connect_card_has_offer_message_box` |
| 55-5 §4 | `t55f_approval_context::test_will_requirement_combines_s_and_u`, `t55f_approval_context::test_will_floor_is_unset_until_measured`, `t55f_approval_context::test_will_floor_excludes_when_set` |
| 55-5 §5 | `t55f_approval_context::test_mutual_requires_will_for_required_people` |

## 照合の段1（指示書56 v3・196〜204）

| 項目 | テスト |
|---|---|
| 196 | `t56_matching_stage1::test_t196_gamma_not_used`, `calibration_v4::test_h2_1_gamma_no_longer_affects_score` |
| 197 | `t56_matching_stage1::test_t197_c_not_used_resonance_is_a_only`, `calibration_v4::test_h2_3_c_channel_no_longer_moves_score` |
| 198 | `t56_matching_stage1::test_t198_directions_judged_separately` |
| 199 | `t56_matching_stage1::test_t199_mutual_only_when_both_directions_pass` |
| 200 | `t56_matching_stage1::test_t200_p_fixed_at_zero`, `calibration_v4::test_h2_3_p_is_fixed_at_zero` |
| 201 | `t56_matching_stage1::test_t201_will_floor_removed` |
| 202 | `t56_matching_stage1::test_t202_no_numbers_in_api_or_screen` |
| 203 | `t56_matching_stage1::test_t203_zero_result_reason_is_own_side_only` |
| 204 | `t56_matching_stage1::test_t204_c_deviation_recorded_in_docs` |

## 照合の段2〜3（指示書57・205〜220）

| 項目 | テスト（`t57_v5::`） |
|---|---|
| 205 | `test_t205_validation_rules[*]`, `test_t205_rule1_schema_version`, `test_t205_valid_v5_draft_saved_and_extra_numbers_dropped`, `test_t205_evidence_checked_against_raw_text`（指示書60 で語りの別欄を廃止） |
| 206 | `test_t206_purpose_ids_server_side_and_stable` |
| 207 | `test_t207_one_event_per_purpose_c3` |
| 208 | `test_t208_offer_change_changes_each_purpose_hash` |
| 209 | `test_t209_profile_structured_canon_version`, `test_t209_c1_c2_rows_still_recompute` |
| 210 | `test_t210_sentence_vectors_saved`, `test_t210_required_sentence_must_be_met` |
| 211・212 | `test_t211_t212_results_grouped_by_purpose_with_quote_pairs` |
| 213 | `test_t213_approval_reason_recomputed_from_refs` |
| 214 | `test_t214_offer_stores_purpose_and_refs_idempotent_per_purpose`, `test_t214_establish_uses_purpose_event_hash` |
| 215 | `test_t215_offer_message_box_only_for_offerer`（57 受理時の訂正 #5: 入力欄は申し出る側だけ） |
| 216 | `test_t216_v4_counterpart_matched_by_state` |
| 217 | `test_t217_trajectory_purposes_and_branch` |
| 218 | `test_t218_wording` |
| 219 | `test_t219_types_do_not_partition_comparison` |
| 220 | `test_t220_one_per_person_assumptions_removed` |
| 221 | `test_t57f_approval_does_not_carry_message` |
| 222 | `test_t57f_exclusion_is_per_purpose` |
| 223 | `test_t223_register_prompts_are_v5_revision2`（指示書60 で改訂2 に合わせて書き換え。改訂3 は 267） |

## 指示書58（テスト4の不整合の解消）— `tests/test_t58.py`

| 項目 | テスト |
|---|---|
| 224 | `test_t224_offer_side_is_never_a_necessity`, `test_t224_offer_half_is_its_own_block` |
| 225 | `test_t225_inbox_loads_reason_component_outside_title`, `test_t225_inbox_uses_session_identity_like_mypage` |
| 226 | `test_t226_zero_results_links_to_inbox_when_offers_pending` |
| 1-3（番号なし） | `test_t58_connect_header_wording` |
| 227 | `test_t227_quote_is_cut_at_sentence_boundary`, `test_t227_no_line_clamp_on_quotes_and_fold_exists` |

## 指示書60（構造化プロンプト v5 改訂2）— `tests/test_t60_prompt_v5r2.py`

| 項目 | テスト |
|---|---|
| 228 | `test_t228_two_prompts_dialogue_is_default` |
| 229 | `test_t229_prompt_text_matches_files_exactly` |
| 230 | `test_t230_no_separate_narrative_field` |
| 231 | `test_t231_handle_notice_near_step2` |
| 232 | `test_t232_zero_offers_accepted` |
| 233 | `test_t233_six_or_more_offers_rejected` |
| 234 | `test_t234_removed_offers_are_not_saved` |
| 235 | `test_t235_all_offers_removed_confirms_with_zero` |
| 236 | `test_t236_no_input_to_add_or_rewrite_offers` |
| 237 | `test_t237_removing_offers_does_not_bump_attempt` |
| 238 | `test_t238_evidence_matches_after_normalization` |
| 239 | `test_t239_missing_evidence_rejected_with_one_line` |
| 240 | `test_t240_evidence_saved_verbatim` |
| 241 | `test_t241_p_sharpness_and_gamma_dropped` |
| 242 | `test_t242_v4_json_still_registers` |
| 改訂2 §3 の 11・12（番号なし） | `test_t60_rules_11_12[*]`, `test_t60_forbidden_wording_does_not_apply_to_quoted_evidence` |

## 指示書61（共鳴の門・根拠の表示・生成元・改訂3）— `tests/test_t61_resonance_gate.py`

| 項目 | テスト |
|---|---|
| 243 | `test_t243_gate_blocks_low_resonance` |
| 244 | `test_t244_no_gate_when_gate_s_zero` |
| 245 | `test_t245_no_gate_when_uncertain` |
| 246 | `test_t246_direction_b_uses_their_purpose_numbers` |
| 247 | `test_t247_resonance_not_mixed_into_score` |
| 248 | `test_t248_gate_dropped_reason_not_shown_or_recorded` |
| 249 | `test_t249_v4_person_uses_will_as_destination` |
| 250 | `test_t250_audit_route_has_gate_values_public_does_not` |
| 251 | `test_t251_card_shows_need_to_offer_pair` |
| 252 | `test_t252_card_never_pairs_needs_with_needs` |
| 253 | `test_t253_approval_shows_direction_b_first` |
| 254 | `test_t254_v4_counterpart_shows_matching_state_slot` |
| 255 | `test_t255_no_similarity_values_on_screen` |
| 256 | `test_t256_revision3_json_accepted` |
| 257 | `test_t257_revision2_json_accepted_alpha_beta_dropped` |
| 258 | `test_t258_empty_generator_rejected_one_line` |
| 259 | `test_t259_slash_joined_evidence_accepted` |
| 260 | `test_t260_slash_part_missing_rejected` |
| 261 | `test_t261_slash_evidence_saved_verbatim` |
| 262 | `test_t262_generator_recorded_as_family_and_tag` |
| 263 | `test_t263_profile_structured_p3_covers_why_and_experience` |
| 264 | `test_t264_necessity_published_c4_without_alpha_beta` |
| 265 | `test_t265_p2_and_c3_events_verify_by_their_rules` |
| 266 | `test_t266_profile_shows_why` |
| 267 | `test_t267_register_prompts_are_revision3` |
| 番号なし | `test_t61_verify_c3_rows_written_with_integer_numbers`（c3 の整数値の検証） |

既存のテストの書き換え（指示書61）: 207（新しい版は c4）・209（v5 の宣言は p3）・218（見出しの文言）・224（見出し）・227（「全文を見る」）

## 指示書62（登録不能の再発防止）— `tests/test_t62_registration.py`

| 項目 | テスト |
|---|---|
| 268 | `test_t268_rev4_code_block_registers` |
| 269 | `test_t269_rev3_without_code_block_registers` |
| 270 | `test_t270_meta_source_values[*]` |
| 271 | `test_t271_slash_separated_quotes_all_present` |
| 272 | `test_t272_newline_separated_quotes_still_pass` |
| 273 | `test_t273_missing_quote_rejected_without_auto_add` |
| 274 | `test_t274_format_repaired_with_notice_and_counts` |
| 275 | `test_t275_no_notice_for_valid_json` |
| 276 | `test_t276_repair_does_not_affect_ledger` |
| 番号なし | `test_t62_copy_guidance_and_parse_error_next_step`、`tests/test_json_repair.py`（#135） |

既存のテストの書き換え（指示書62）: 238（比較の正規化を NFC と前後の空白だけに）・267（rev4）・205・239・260（根拠の文言）

## 指示書63 段階1（v5 表示の整理）

### PR-A（v5 の人の編集・再試行を止める）— `tests/test_t63a_v5_edit_guard.py`

| 項目 | テスト |
|---|---|
| 277 | `test_t277_v5_edit_and_retry_blocked` |
| 278 | `test_t278_v4_job_writes_nothing_for_v5` |
| 279 | `test_t279_purposeless_necessity_refused_for_v5` |
| 280 | `test_t280_v4_edit_and_retry_unchanged` |
| 281 | `test_t281_mypage_shows_rebuild_for_v5` |
| 番号なし | `test_t63a_v4_json_paste_refused_for_v5` |

### PR-B（監査ルートに文単位の対）— `tests/test_t63b_audit_sentences.py`

| 項目 | テスト |
|---|---|
| 282 | `test_t282_sentences_cover_all_needs_and_match_judgement` |
| 283 | `test_t283_top3_sorted_desc_at_most_three` |
| 284 | `test_t284_no_raw_text_evidence_or_generator` |
| 285 | `test_t285_token_required` |
| 286 | `test_t286_without_detail_unchanged` |
| 番号なし | `test_t63b_v4_counterpart_full_state_and_display_field` |

### PR-C（プロフィールの v5 表示）— `tests/test_t63c_profile_v5.py`

| 項目 | テスト |
|---|---|
| 287 | `test_t287_purposes_interest_means_for_both_offers_owner_only` |
| 288 | `test_t288_must_mark` |
| 289 | `test_t289_threshold_hides_needs_from_third_parties` |
| 290 | `test_t290_v4_profile_unchanged` |
| 291 | `test_t291_no_evidence_numbers_raw_for_third_parties` |
| 292 | `test_t292_trajectory_offers_not_for_third_parties` |
| 293 | `test_t293_order_and_owner_page` |

既存のテストの書き換え（指示書63 段階1 PR-C）: 216（マイページの 1 行の文言を「力になれること」の画面名に）

### PR-D（受信箱のカード化・承認を1か所に・見送る・経路の記録）— `tests/test_t63d_inbox_decline.py`

| 項目 | テスト |
|---|---|
| 294 | `test_t294_decline_only_in_normal_db` |
| 295 | `test_t295_exclusion_continues_after_decline` |
| 296 | `test_t296_reoffer_blocked_until_new_version`・`test_t296b_new_version_by_offerer_also_lifts` |
| 297 | `test_t297_only_receiver_can_decline` |
| 298 | `test_t298_offerer_sees_declined` |
| 299 | `test_t299_channel_recorded_not_shown` |
| 300 | `test_t300_card_reason_fields_one_way`・`test_t300b_mutual_direction_b_first_with_purpose`・`test_t300c_offer_time_basis_after_offerer_restructures` |
| 301 | `test_t301_v4_counterpart_whole_state_note` |
| 302 | `test_t302_no_reason` |
| 303 | `test_t303_single_approval_place` |

既存のテストの書き換え（指示書63 段階1 PR-D。承認の場面を受信箱だけにし、見出しを「応えるもの」に）:
253・213・215・`test_approval_screens_show_reason_and_offer_message`（55-5）

### PR-E（つながるのカード・一覧のラベル）— `tests/test_t63e_connect_cards.py`

| 項目 | テスト |
|---|---|
| 304 | `test_t304_match_card_rows_and_for_purpose` |
| 305 | `test_t305_no_offers_evidence_numbers_on_cards` |
| 306 | `test_t306_threshold_on_cards_and_directory` |
| 307 | `test_t307_directory_rows` |
| 308 | `test_t308_labels_order_and_note` |
| 309 | `test_t309_v4_card` |

既存のテストの書き換え（指示書63 段階1 PR-E。カードの段を足した）: 162（照合の結果のキー）・169/171（/seekers のキー）
