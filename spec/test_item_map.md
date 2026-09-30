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
