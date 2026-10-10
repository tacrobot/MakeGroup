/**
 * 公平なグループ分けシステム - Google Apps Script
 * 仕様書 §5 に基づく実装。
 *
 * 使い方:
 *   1. このファイルのコードを、管理用スプレッドシートのスクリプトエディタに貼り付ける。
 *   2. スプレッドシートを再読み込みすると「グループ分け」メニューが表示される。
 *   3. 参加者シートに ID と名前を入力してから「フォームを作成」を実行する。
 */

// ============================================================
// 定数
// ============================================================

/** 5段階評価の選択肢と評価点のマッピング（§3） */
const SCORE_MAP = {
  ぜひ同じグループになりたい: 2,
  できれば同じグループになりたい: 1,
  どちらでもよい: 0,
  できれば別のグループがよい: -1,
  別のグループがよい: -2,
};

/** スコアラベルのリスト（フォームの選択肢順） */
const SCORE_LABELS = Object.keys(SCORE_MAP);

/** スプレッドシートのシート名 */
const SHEET_PARTICIPANTS = "participants";
const SHEET_FORM_CONFIG = "form_config";
const SHEET_FORM_RESPONSES = "form_responses";
const SHEET_MASTER_RATINGS = "master_ratings";
const SHEET_IMPORT_LOG = "import_log";

// ============================================================
// フォーム作成の分割実行用定数
// ============================================================

/** PropertiesService のキー */
const CF_FORM_ID = "cf_form_id";
const CF_SS_ID = "cf_ss_id";
const CF_PARTICIPANTS = "cf_participants";
const CF_DIGEST = "cf_digest";
const CF_NEXT_SECTION = "cf_next_section";
const CF_STATUS = "cf_status"; // "building" | "done" | "error"
const CF_ERROR_MSG = "cf_error_msg";
const CF_FORM_URL = "cf_form_url";

/** 1回の実行でセクション追加を打ち切る時間（ミリ秒） */
const CF_TIME_LIMIT_MS = 240000; // 4分（6分制限に対する余裕）

/** 続行用トリガーの関数名 */
const CF_CONTINUE_FN = "_continueCreateForm";

/** 続行トリガーの待機時間（ミリ秒） */
const CF_TRIGGER_DELAY_MS = 60000; // 1分後

// ============================================================
// メニュー（§5.1）
// ============================================================

/**
 * スプレッドシートを開いたときにメニューを追加する。
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("グループ分け")
    .addItem("フォームを作成", "createForm")
    .addItem("フォーム作成の状態を確認", "checkCreateFormStatus")
    .addSeparator()
    .addItem("評価データを再集計", "rebuildMasterRatings")
    .addItem("回答受付を締め切る", "closeForm")
    .addItem("回答受付を再開する", "reopenForm")
    .addToUi();
}

// ============================================================
// フォーム作成（§5.2）
// ============================================================

/**
 * フォームを作成する。
 * 実行のたびに新しいフォームを作る。以前のフォームは削除しない。
 *
 * 参加者数が多く1回の実行で制限時間を超える場合は、処理を分割して実行する。
 * 1回目: フォームの基本設定と回答者選択質問を作成し、セクションを可能な分だけ追加する。
 * 2回目以降: 時間ベーストリガーで自動的に続きを実行し、残りのセクションを追加する。
 * 全セクション追加完了後: 分岐設定・回答先設定・form_config書き込み・トリガー設置を行う。
 */
function createForm() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const props = PropertiesService.getScriptProperties();

  // 進行中のフォーム作成があるか確認
  const prevStatus = props.getProperty(CF_STATUS);
  if (prevStatus === "building") {
    const ui = SpreadsheetApp.getUi();
    const response = ui.alert(
      "フォームの作成が進行中です。\n\n新しいフォームの作成を開始しますか？\n" +
        "（進行中の作りかけのフォームは残ります）",
      ui.ButtonSet.YES_NO,
    );
    if (response !== ui.Button.YES) return;
  } else if (prevStatus === "error") {
    const errorMsg = props.getProperty(CF_ERROR_MSG) || "不明なエラー";
    SpreadsheetApp.getUi().alert(
      "前回のフォーム作成でエラーが発生しました。\n\nエラー: " +
        errorMsg +
        "\n\n新しいフォームの作成を開始します。",
    );
  }

  // participants シートを読み込む
  const participants = _loadParticipants(ss);
  if (!participants) return; // エラーは _loadParticipants 内で表示済み

  // 参加者の検証（§5.2 手順1）
  const validationError = _validateParticipantsForForm(participants);
  if (validationError) {
    SpreadsheetApp.getUi().alert(
      "フォームの作成を中止しました。\n\n" + validationError,
    );
    return;
  }

  const n = participants.length;

  // form_config の現在の participants_digest と比較するために記録しておく
  const currentDigest = _computeParticipantsDigest(participants);

  // 古い状態をクリア
  _clearCreateFormState();

  // フォームの基本設定と回答者選択質問を作成
  let form;
  try {
    form = _createFormBase(participants);
  } catch (e) {
    // 途中で失敗した場合、form_config は書き換えない（§5.2）
    SpreadsheetApp.getUi().alert(
      "フォームの作成中にエラーが発生しました。form_config は変更していません。\n\n" +
        "作りかけのフォームは手動で削除してください。\n\nエラー: " +
        e.message,
    );
    _clearCreateFormState();
    return;
  }

  // 状態を保存
  props.setProperty(CF_FORM_ID, form.getId());
  props.setProperty(CF_SS_ID, ss.getId());
  props.setProperty(CF_PARTICIPANTS, JSON.stringify(participants));
  props.setProperty(CF_DIGEST, currentDigest);
  props.setProperty(CF_NEXT_SECTION, "0");
  props.setProperty(CF_STATUS, "building");

  // セクションの追加を開始
  const startTime = Date.now();
  let nextSection;
  try {
    nextSection = _addFormSections(form, participants, 0, startTime);
  } catch (e) {
    SpreadsheetApp.getUi().alert(
      "フォームの作成中にエラーが発生しました。form_config は変更していません。\n\n" +
        "作りかけのフォームは手動で削除してください。\n\nエラー: " +
        e.message,
    );
    _clearCreateFormState();
    return;
  }

  if (nextSection >= n) {
    // 全セクション追加完了 → 最終処理（UIあり）
    _finalizeCreateForm();
  } else {
    // 時間切れ → 続行トリガーをスケジュール
    props.setProperty(CF_NEXT_SECTION, String(nextSection));
    _scheduleCreateFormContinuation();
    SpreadsheetApp.getUi().alert(
      "フォームの作成を開始しました。\n\n" +
        "参加者数が多いため、作成処理を分割して実行します。\n" +
        "自動的に続きを実行しますので、しばらくお待ちください。\n" +
        "完了すると、form_config シートにフォームの URL が記録されます。\n\n" +
        "進捗は「フォーム作成の状態を確認」で確認できます。",
    );
  }
}

/**
 * フォームの基本設定と回答者選択質問を作成する（§4.2）。
 * セクションは作成せず、フォームと1ページ目の質問のみを返す。
 */
function _createFormBase(participants) {
  const form = FormApp.create("グループ分けアンケート");
  form.setDescription(
    "参加者同士の相性をもとにグループを決めるためのアンケートです。" +
      "回答は他の参加者には公開しません。" +
      "最初に自分の名前を選ぶと、自分用のページに進みます。" +
      "自分以外の全員について、同じグループになることをどう思うかを選んでください。" +
      "さらに、どうしても同じグループになりたくない人がいる場合に限り、同席不可として選べます。" +
      "同席不可に選んだ人とは、必ず別のグループになります。" +
      "同じ人が2回送信した場合は、最後に送信した回答だけを使います。",
  );
  form.setProgressBar(true);   // プログレスバーを表示する
  form.setShuffleQuestions(false);
  form.setCollectEmail(false);
  form.setLimitOneResponsePerUser(false);
  form.setAllowResponseEdits(false);
  form.setPublishingSummary(false);

  // 1ページ目: 回答者を選ぶ質問（§4.2）
  const raterItem = form.addListItem();
  raterItem.setTitle("あなたの名前を選んでください");
  raterItem.setHelpText(
    "必ず自分の名前を選んでください。" +
      "選び間違えた場合は、もう一度最初から回答し直してください。" +
      "最後に送信した回答が有効になります。",
  );
  raterItem.setRequired(true);
  raterItem.setChoices(participants.map((p) => raterItem.createChoice(p.name)));

  return form;
}

/**
 * フォームに回答者用セクションを追加する（§4.3）。
 * startIdx から順にセクションを追加し、時間制限に達したら中断する。
 *
 * @param {Form} form フォームオブジェクト
 * @param {Array} participants 参加者リスト
 * @param {number} startIdx 追加を開始するセクションインデックス
 * @param {number} startTime 実行開始時刻（ミリ秒）
 * @return {number} 次に追加すべきセクションインデックス（全完了時は participants.length）
 */
function _addFormSections(form, participants, startIdx, startTime) {
  const n = participants.length;

  for (let ri = startIdx; ri < n; ri++) {
    // 時間制限チェック（セクション追加前に判定）
    if (Date.now() - startTime > CF_TIME_LIMIT_MS) {
      return ri; // このセクションは追加していない
    }

    const rater = participants[ri];
    const others = participants.filter((_, idx) => idx !== ri);

    // セクションの開始
    const section = form.addPageBreakItem();
    section.setTitle(rater.name + "さん用の回答ページ");
    section.setHelpText(
      "自分以外の" +
        (n - 1) +
        "人について、同じグループになることをどう思うかを選んでください。" +
        "5段階の評価は、すべて必須です。" +
        "ページの最後では、どうしても同じグループになりたくない人がいる場合に限り、その人を選んでください。" +
        "選んだ人とは、必ず別のグループになります。",
    );

    // 5段階評価（ID昇順の他全員）
    for (const target of others) {
      const ratingItem = form.addMultipleChoiceItem();
      ratingItem.setTitle(
        target.name + "さんと同じグループになることを、どう思いますか？",
      );
      ratingItem.setRequired(true);
      ratingItem.setChoices(
        SCORE_LABELS.map((label) => ratingItem.createChoice(label)),
      );
    }

    // 同席不可の質問
    const incompatibleItem = form.addCheckboxItem();
    incompatibleItem.setTitle("同席不可を希望する人");
    incompatibleItem.setHelpText(
      "どうしても同じグループになりたくない人がいる場合に限り、選んでください。" +
        "選ばなくても送信できます。" +
        "選んだ人とは、必ず別のグループになります。" +
        "5段階評価の「別のグループがよい」とは、別の項目です。",
    );
    incompatibleItem.setRequired(false);
    incompatibleItem.setChoices(
      others.map((p) => incompatibleItem.createChoice(p.name)),
    );
  }

  return n; // 全セクション追加完了
}

/**
 * トリガーから呼ばれる続きの処理。
 * 保存された状態を読み込み、セクション追加を再開する。
 * 全セクション完了時に最終処理へ進む。
 */
function _continueCreateForm() {
  const startTime = Date.now();
  const props = PropertiesService.getScriptProperties();

  const status = props.getProperty(CF_STATUS);
  if (status !== "building") return; // 進行中でなければ何もしない

  try {
    const formId = props.getProperty(CF_FORM_ID);
    const participants = JSON.parse(props.getProperty(CF_PARTICIPANTS));
    const nextSection = parseInt(props.getProperty(CF_NEXT_SECTION), 10);

    const form = FormApp.openById(formId);
    const completed = _addFormSections(
      form,
      participants,
      nextSection,
      startTime,
    );

    if (completed >= participants.length) {
      // 全セクション追加完了 → 最終処理
      _finalizeCreateForm();
    } else {
      // まだ残りがある → 状態を更新してトリガーをスケジュール
      props.setProperty(CF_NEXT_SECTION, String(completed));
      _scheduleCreateFormContinuation();
    }
  } catch (e) {
    // 续行中のエラーは状態に記録し、トリガーを停止
    props.setProperty(CF_STATUS, "error");
    props.setProperty(CF_ERROR_MSG, e.message);
    _deleteCreateFormTriggers();
    console.error("フォーム作成の续行中にエラー: " + e.message);
  }
}

/**
 * フォーム作成の最終処理（§5.2 手順3〜6）。
 * 分岐設定、回答先設定、form_config書き込み、トリガー設置を行う。
 * トリガーから実行された場合はUIアラートが使えないため、
 * 結果はform_configシートとPropertiesServiceに記録する。
 */
function _finalizeCreateForm() {
  const props = PropertiesService.getScriptProperties();
  const formId = props.getProperty(CF_FORM_ID);
  const ssId = props.getProperty(CF_SS_ID);
  const participants = JSON.parse(props.getProperty(CF_PARTICIPANTS));
  const digest = props.getProperty(CF_DIGEST);

  const ss = ssId
    ? SpreadsheetApp.openById(ssId)
    : SpreadsheetApp.getActiveSpreadsheet();
  const form = FormApp.openById(formId);

  try {
    // 分岐設定のため、フォームのアイテムから必要なものを取得
    const items = form.getItems();
    const sectionItems = [];
    let raterItem = null;

    for (const item of items) {
      if (item.getType() === FormApp.ItemType.LIST) {
        raterItem = item.asListItem();
      } else if (item.getType() === FormApp.ItemType.PAGE_BREAK) {
        sectionItems.push(item.asPageBreakItem());
      }
    }

    // 各セクションの分岐設定（§4.3 最後）
    // セクション完了後は「フォームを送信」へ
    for (const section of sectionItems) {
      section.setGoToPage(FormApp.PageNavigationType.SUBMIT);
    }

    // 1ページ目の分岐設定: 名前→セクション
    raterItem.setChoices(
      participants.map((p, ri) =>
        raterItem.createChoice(p.name, sectionItems[ri]),
      ),
    );

    // 回答先をスプレッドシートに設定（§5.2 手順3）
    form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
    // 回答シートの名前変更を試みる（失敗してもよい）
    try {
      Utilities.sleep(2000); // シートが作られるまで少し待つ
      const sheets = ss.getSheets();
      for (const sheet of sheets) {
        if (
          sheet.getName().startsWith("フォームの回答") ||
          sheet.getName() === "Form Responses 1"
        ) {
          sheet.setName(SHEET_FORM_RESPONSES);
          break;
        }
      }
    } catch (e) {
      // 名前変更の失敗は無視する
    }

    // form_config を書き直す（§5.2 手順4）
    _writeFormConfig(ss, form, participants, digest);

    // フォーム送信時のトリガーを設置（§5.2 手順5）
    _setupFormTrigger(form);

    // 完了状態を記録（§5.2 手順6）
    const url = form.getPublishedUrl();
    const shortUrl = form.shortenFormUrl(url);
    props.setProperty(CF_STATUS, "done");
    props.setProperty(CF_FORM_URL, shortUrl);

    // 続行トリガーがあれば削除
    _deleteCreateFormTriggers();

    // UIが使える場合はアラートを表示
    try {
      SpreadsheetApp.getUi().alert(
        "フォームを作成しました！\n\n参加者に共有する URL:\n" + shortUrl,
      );
    } catch (e) {
      // トリガーから実行している場合はUIが使えない
      // フォームURLはform_configに記録済み
      console.log("フォーム作成完了: " + shortUrl);
    }
  } catch (e) {
    // 最終処理でエラーが発生した場合
    props.setProperty(CF_STATUS, "error");
    props.setProperty(CF_ERROR_MSG, e.message);
    _deleteCreateFormTriggers();
    console.error("フォーム作成の最終処理でエラー: " + e.message);

    // UIが使える場合はアラートを表示
    try {
      SpreadsheetApp.getUi().alert(
        "フォームの最終処理中にエラーが発生しました。" +
          "form_config は変更していない可能性があります。\n\n" +
          "作りかけのフォームは手動で削除してください。\n\nエラー: " +
          e.message,
      );
    } catch (uiErr) {
      console.error("UI表示エラー: " + uiErr.message);
    }
  }
}

/**
 * フォーム作成の続行用トリガーをスケジュールする。
 * 既存の续行トリガーがあれば先に削除する。
 */
function _scheduleCreateFormContinuation() {
  _deleteCreateFormTriggers();

  ScriptApp.newTrigger(CF_CONTINUE_FN)
    .timeBased()
    .after(CF_TRIGGER_DELAY_MS)
    .create();
}

/**
 * フォーム作成の续行用トリガーを削除する。
 */
function _deleteCreateFormTriggers() {
  const triggers = ScriptApp.getProjectTriggers();
  for (const trigger of triggers) {
    if (trigger.getHandlerFunction() === CF_CONTINUE_FN) {
      ScriptApp.deleteTrigger(trigger);
    }
  }
}

/**
 * フォーム作成の状態をすべてクリアする。
 * PropertiesServiceのキーを削除し、续行トリガーも削除する。
 */
function _clearCreateFormState() {
  const props = PropertiesService.getScriptProperties();
  props.deleteProperty(CF_FORM_ID);
  props.deleteProperty(CF_SS_ID);
  props.deleteProperty(CF_PARTICIPANTS);
  props.deleteProperty(CF_DIGEST);
  props.deleteProperty(CF_NEXT_SECTION);
  props.deleteProperty(CF_STATUS);
  props.deleteProperty(CF_ERROR_MSG);
  props.deleteProperty(CF_FORM_URL);
  _deleteCreateFormTriggers();
}

/**
 * フォーム作成の状態を確認して画面に表示する。
 */
function checkCreateFormStatus() {
  const props = PropertiesService.getScriptProperties();
  const status = props.getProperty(CF_STATUS);
  const ui = SpreadsheetApp.getUi();

  if (!status) {
    ui.alert("フォームの作成は開始されていません。");
    return;
  }

  if (status === "building") {
    const nextSection = props.getProperty(CF_NEXT_SECTION);
    const participants = JSON.parse(props.getProperty(CF_PARTICIPANTS));
    ui.alert(
      "フォームを作成中です。\n\n" +
        "セクション " +
        nextSection +
        " / " +
        participants.length +
        " まで完了しています。\n" +
        "自動的に续行されますので、しばらくお待ちください。",
    );
  } else if (status === "done") {
    const formUrl = props.getProperty(CF_FORM_URL);
    ui.alert(
      "フォームの作成が完了しています。\n\n参加者に共有する URL:\n" + formUrl,
    );
  } else if (status === "error") {
    const errorMsg = props.getProperty(CF_ERROR_MSG) || "不明なエラー";
    ui.alert(
      "フォームの作成でエラーが発生しました。\n\n" +
        "エラー: " +
        errorMsg +
        "\n\n作りかけのフォームは手動で削除してください。",
    );
  }
}

// ============================================================
// form_config シート（§5.3）
// ============================================================

/**
 * form_config シートを書き直す。
 */
function _writeFormConfig(ss, form, participants, digest) {
  let sheet = ss.getSheetByName(SHEET_FORM_CONFIG);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_FORM_CONFIG);
  }
  sheet.clearContents();

  // ヘッダー
  sheet.appendRow(["item_type", "item_id", "rater_id", "target_id", "value"]);

  // form_id, form_url, participants_digest
  sheet.appendRow(["form_id", "", "", "", form.getId()]);
  sheet.appendRow(["form_url", "", "", "", form.getPublishedUrl()]);
  sheet.appendRow(["participants_digest", "", "", "", digest]);

  // フォームのアイテムを取得
  const items = form.getItems();

  // アイテムの種類ごとに記録
  // 最初の ListItem が rater_select
  // その後に参加者ごとのセクション・rating・incompatible が続く

  let raterSelectRecorded = false;
  let raterIdx = -1;
  const n = participants.length;

  for (const item of items) {
    const itemType = item.getType();
    const itemId = String(item.getId());

    if (!raterSelectRecorded && itemType === FormApp.ItemType.LIST) {
      sheet.appendRow(["rater_select", itemId, "", "", ""]);
      raterSelectRecorded = true;
      continue;
    }

    if (itemType === FormApp.ItemType.PAGE_BREAK) {
      raterIdx++;
      if (raterIdx < n) {
        sheet.appendRow(["section", itemId, participants[raterIdx].id, "", ""]);
      }
      continue;
    }

    if (
      itemType === FormApp.ItemType.MULTIPLE_CHOICE &&
      raterIdx >= 0 &&
      raterIdx < n
    ) {
      // 5段階評価: タイトルから対象者を特定
      const title = item.getTitle();
      for (const target of participants) {
        if (
          target.id !== participants[raterIdx].id &&
          title.startsWith(target.name + "さん")
        ) {
          sheet.appendRow([
            "rating",
            itemId,
            participants[raterIdx].id,
            target.id,
            "",
          ]);
          break;
        }
      }
      continue;
    }

    if (
      itemType === FormApp.ItemType.CHECKBOX &&
      raterIdx >= 0 &&
      raterIdx < n
    ) {
      sheet.appendRow([
        "incompatible",
        itemId,
        participants[raterIdx].id,
        "",
        "",
      ]);
      continue;
    }
  }
}

// ============================================================
// フォームトリガー（§5.1）
// ============================================================

/**
 * フォーム送信時のトリガーを設置する。
 * 既存の同名トリガーがあれば先に削除する。
 */
function _setupFormTrigger(form) {
  // 既存トリガーを削除
  const triggers = ScriptApp.getProjectTriggers();
  for (const trigger of triggers) {
    if (trigger.getHandlerFunction() === "onFormSubmit") {
      ScriptApp.deleteTrigger(trigger);
    }
  }

  // 新しいトリガーを設置
  ScriptApp.newTrigger("onFormSubmit").forForm(form).onFormSubmit().create();
}

/**
 * フォーム送信時のトリガーハンドラ。
 * 引数 e は使わない。
 */
function onFormSubmit(e) {
  rebuildMasterRatings();
}

// ============================================================
// 評価データの集計（§5.4）
// ============================================================

/**
 * master_ratings を全回答から作り直す。
 */
function rebuildMasterRatings() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // 排他制御（§5.4 手順1）
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(30000);
  } catch (e) {
    console.log("ロックを取得できませんでした。集計をスキップします。");
    return;
  }

  try {
    // form_config の存在確認と participants_digest の照合（§5.4 手順2）
    const configSheet = ss.getSheetByName(SHEET_FORM_CONFIG);
    if (!configSheet) {
      SpreadsheetApp.getUi().alert(
        "form_config シートが見つかりません。先に「フォームを作成」を実行してください。",
      );
      return;
    }

    const participants = _loadParticipants(ss);
    if (!participants) return;

    const currentDigest = _computeParticipantsDigest(participants);
    const storedDigest = _getFormConfigValue(
      configSheet,
      "participants_digest",
    );
    if (!storedDigest || storedDigest !== currentDigest) {
      SpreadsheetApp.getUi().alert(
        "participants の内容がフォーム作成時から変更されています。\n" +
          "「フォームを作成」を実行し直してから再集計してください。",
      );
      return;
    }

    // フォーム ID を取得
    const formId = _getFormConfigValue(configSheet, "form_id");
    if (!formId) {
      SpreadsheetApp.getUi().alert(
        "form_config に form_id が記録されていません。",
      );
      return;
    }

    const form = FormApp.openById(formId);
    const responses = form.getResponses();
    const n = participants.length;
    const nameToId = {};
    for (const p of participants) {
      nameToId[p.name] = p.id;
    }

    // form_config からアイテムIDの対応表を構築
    const configData = configSheet.getDataRange().getValues();
    const ratingMap = {}; // {rater_id: {item_id: target_id}}
    const incompatibleMap = {}; // {rater_id: item_id}
    const raterSelectId = _getFormConfigValue(configSheet, "rater_select");

    for (const row of configData) {
      const [itemType, itemId, raterId, targetId] = row;
      if (itemType === "rating") {
        if (!ratingMap[raterId]) ratingMap[raterId] = {};
        ratingMap[raterId][itemId] = targetId;
      } else if (itemType === "incompatible") {
        incompatibleMap[raterId] = itemId;
      }
    }

    // 回答を処理（§5.4 手順3〜4）
    const responseLog = []; // {response_id, submitted_at, rater_id, status, reason}
    const raterLastResponse = {}; // {rater_id: response_index}

    for (let idx = 0; idx < responses.length; idx++) {
      const resp = responses[idx];
      const responseId = resp.getId();
      const submittedAt = _formatJST(resp.getTimestamp());

      // 手順3.1: 回答者の名前から ID を決める
      const itemResponses = resp.getItemResponses();
      let raterName = null;
      for (const ir of itemResponses) {
        if (String(ir.getItem().getId()) === raterSelectId) {
          raterName = ir.getResponse();
          break;
        }
      }

      if (!raterName) {
        responseLog.push({
          response_id: responseId,
          submitted_at: submittedAt,
          rater_id: "",
          status: "invalid",
          reason: "rater_not_selected",
        });
        continue;
      }

      const raterId = nameToId[raterName];
      if (!raterId) {
        responseLog.push({
          response_id: responseId,
          submitted_at: submittedAt,
          rater_id: "",
          status: "invalid",
          reason: "unknown_choice",
        });
        continue;
      }

      // 手順3.2: rater_mismatch の確認
      let raterMismatch = false;
      for (const ir of itemResponses) {
        const itemId = String(ir.getItem().getId());
        for (const [ownRaterId, targetMap] of Object.entries(ratingMap)) {
          if (targetMap[itemId] !== undefined && ownRaterId !== raterId) {
            raterMismatch = true;
            break;
          }
        }
        if (raterMismatch) break;
      }
      if (raterMismatch) {
        responseLog.push({
          response_id: responseId,
          submitted_at: submittedAt,
          rater_id: raterId,
          status: "invalid",
          reason: "rater_mismatch",
        });
        continue;
      }

      // 手順3.3: 5段階評価の件数確認
      const myRatingMap = ratingMap[raterId] || {};
      const answeredRatings = {};
      for (const ir of itemResponses) {
        const itemId = String(ir.getItem().getId());
        if (myRatingMap[itemId] !== undefined) {
          answeredRatings[itemId] = ir.getResponse();
        }
      }

      const expectedTargets = new Set(Object.values(myRatingMap));
      const answeredTargets = new Set(
        Object.keys(answeredRatings).map((id) => myRatingMap[id]),
      );

      if (answeredTargets.size !== n - 1 || answeredTargets.has(raterId)) {
        responseLog.push({
          response_id: responseId,
          submitted_at: submittedAt,
          rater_id: raterId,
          status: "invalid",
          reason: "incomplete",
        });
        continue;
      }

      // 手順3.4: 評価点の変換
      let unknownChoice = false;
      const scoreData = {};
      for (const [itemId, targetId] of Object.entries(myRatingMap)) {
        const label = answeredRatings[itemId];
        if (label === undefined || SCORE_MAP[label] === undefined) {
          unknownChoice = true;
          break;
        }
        scoreData[targetId] = SCORE_MAP[label];
      }
      if (unknownChoice) {
        responseLog.push({
          response_id: responseId,
          submitted_at: submittedAt,
          rater_id: raterId,
          status: "invalid",
          reason: "unknown_choice",
        });
        continue;
      }

      // 手順3.5: 同席不可の処理
      const incompatibleItemId = incompatibleMap[raterId];
      const incompatibleTargets = new Set();
      for (const ir of itemResponses) {
        if (String(ir.getItem().getId()) === incompatibleItemId) {
          const selected = ir.getResponse(); // 配列
          if (Array.isArray(selected)) {
            for (const name of selected) {
              const tid = nameToId[name];
              if (!tid || tid === raterId) {
                unknownChoice = true;
                break;
              }
              incompatibleTargets.add(tid);
            }
          }
          break;
        }
      }
      if (unknownChoice) {
        responseLog.push({
          response_id: responseId,
          submitted_at: submittedAt,
          rater_id: raterId,
          status: "invalid",
          reason: "unknown_choice",
        });
        continue;
      }

      // 有効な回答として記録
      responseLog.push({
        response_id: responseId,
        submitted_at: submittedAt,
        rater_id: raterId,
        status: "valid",
        reason: "",
        scoreData: scoreData,
        incompatibleTargets: incompatibleTargets,
        response_obj: resp,
      });
      raterLastResponse[raterId] = idx;
    }

    // 手順4: 回答者ごとに最後の有効回答を accepted に
    const acceptedMap = {}; // {rater_id: log_entry}
    for (const entry of responseLog) {
      if (entry.status === "valid") {
        const rid = entry.rater_id;
        if (!acceptedMap[rid]) {
          acceptedMap[rid] = [];
        }
        acceptedMap[rid].push(entry);
      }
    }

    const finalLog = [];
    for (const entry of responseLog) {
      if (entry.status !== "valid") {
        finalLog.push(entry);
        continue;
      }
      const rid = entry.rater_id;
      const all = acceptedMap[rid];
      const last = all[all.length - 1];
      if (entry === last) {
        finalLog.push({ ...entry, status: "accepted" });
      } else {
        finalLog.push({
          ...entry,
          status: "superseded",
          reason: "newer_response_exists",
        });
      }
    }

    // 手順5〜7: master_ratings の構築
    const masterRows = [
      [
        "rater_id",
        "target_id",
        "score",
        "incompatible",
        "response_id",
        "submitted_at",
      ],
    ];

    const sortedRaterIds = participants
      .filter((p) => acceptedMap[p.id])
      .map((p) => p.id)
      .sort();

    for (const raterId of sortedRaterIds) {
      const accepted = acceptedMap[raterId];
      if (!accepted || accepted.length === 0) continue;
      const entry = accepted[accepted.length - 1];

      const targetsSorted = participants
        .filter((p) => p.id !== raterId)
        .map((p) => p.id)
        .sort();

      for (const targetId of targetsSorted) {
        const score =
          entry.scoreData[targetId] !== undefined
            ? entry.scoreData[targetId]
            : 0;
        const incompatible = entry.incompatibleTargets.has(targetId)
          ? "true"
          : "false";
        masterRows.push([
          raterId,
          targetId,
          score,
          incompatible,
          entry.response_id,
          entry.submitted_at,
        ]);
      }
    }

    // master_ratings シートを書き直す
    let masterSheet = ss.getSheetByName(SHEET_MASTER_RATINGS);
    if (!masterSheet) {
      masterSheet = ss.insertSheet(SHEET_MASTER_RATINGS);
    }
    masterSheet.clearContents();
    if (masterRows.length > 0) {
      masterSheet
        .getRange(1, 1, masterRows.length, masterRows[0].length)
        .setValues(masterRows);
    }

    // 手順8: missing の記録
    const missingIds = participants
      .filter((p) => !acceptedMap[p.id])
      .map((p) => p.id);

    // 手順9: import_log を書き直す
    _writeImportLog(ss, finalLog, missingIds);

    // 手順10: 画面表示
    const acceptedCount = Object.keys(acceptedMap).length;
    let message = `受理済み ${acceptedCount}人 / ${n}人`;
    if (missingIds.length > 0) {
      message += "\n\n未回答: " + missingIds.join(", ");
    }
    console.log(message);
  } finally {
    lock.releaseLock();
  }
}

// ============================================================
// import_log シート（§5.5）
// ============================================================

/**
 * import_log シートを書き直す。
 * 評価点と同席不可の指定は書かない（§11）。
 */
function _writeImportLog(ss, logEntries, missingIds) {
  let logSheet = ss.getSheetByName(SHEET_IMPORT_LOG);
  if (!logSheet) {
    logSheet = ss.insertSheet(SHEET_IMPORT_LOG);
  }
  logSheet.clearContents();

  const rows = [
    ["response_id", "submitted_at", "rater_id", "status", "reason"],
  ];
  for (const entry of logEntries) {
    if (entry.status === "valid") continue; // valid は accepted か superseded に変換済み
    rows.push([
      entry.response_id || "",
      entry.submitted_at || "",
      entry.rater_id || "",
      entry.status,
      entry.reason || "",
    ]);
  }

  // missing の行
  for (const mid of missingIds.sort()) {
    rows.push(["", "", mid, "missing", "no_valid_response"]);
  }

  if (rows.length > 0) {
    logSheet.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  }
}

// ============================================================
// フォームの開閉（§5.1）
// ============================================================

/**
 * フォームの回答受付を停止する。
 */
function closeForm() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const formId = _getFormId(ss);
  if (!formId) {
    SpreadsheetApp.getUi().alert(
      "フォームが見つかりません。先にフォームを作成してください。",
    );
    return;
  }
  const form = FormApp.openById(formId);
  form.setAcceptingResponses(false);
  SpreadsheetApp.getUi().alert("回答受付を締め切りました。");
}

/**
 * フォームの回答受付を再開する。
 */
function reopenForm() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const formId = _getFormId(ss);
  if (!formId) {
    SpreadsheetApp.getUi().alert(
      "フォームが見つかりません。先にフォームを作成してください。",
    );
    return;
  }
  const form = FormApp.openById(formId);
  form.setAcceptingResponses(true);
  SpreadsheetApp.getUi().alert("回答受付を再開しました。");
}

// ============================================================
// ユーティリティ
// ============================================================

/**
 * participants シートから参加者リストを読み込む。
 * 失敗時は null を返す（エラーは呼び出し側で表示）。
 */
function _loadParticipants(ss) {
  const sheet = ss.getSheetByName(SHEET_PARTICIPANTS);
  if (!sheet) {
    SpreadsheetApp.getUi().alert(
      "「" + SHEET_PARTICIPANTS + "」シートが見つかりません。",
    );
    return null;
  }

  const data = sheet.getDataRange().getValues();
  if (data.length < 2) {
    SpreadsheetApp.getUi().alert("participants シートにデータがありません。");
    return null;
  }

  const participants = [];
  for (let i = 1; i < data.length; i++) {
    const id = String(data[i][0]).trim();
    const name = String(data[i][1]).trim();
    if (id && name) {
      participants.push({ id, name });
    }
  }

  return participants;
}

/**
 * participants の検証（§5.2 手順1）。
 * エラーがあればエラーメッセージを返す。なければ null を返す。
 */
function _validateParticipantsForForm(participants) {
  const idPattern = /^[0-9]{3}$/;
  const ids = new Set();
  const names = new Set();
  let prevId = null;

  for (const p of participants) {
    if (!idPattern.test(p.id)) {
      return `ID「${p.id}」が ^[0-9]{3}$ の形式ではありません。`;
    }
    if (ids.has(p.id)) {
      return `ID「${p.id}」が重複しています。`;
    }
    ids.add(p.id);

    if (!p.name) {
      return `ID「${p.id}」の名前が空です。`;
    }
    if (names.has(p.name)) {
      return `名前「${p.name}」が重複しています。`;
    }
    names.add(p.name);

    if (prevId !== null && p.id < prevId) {
      return `ID が昇順になっていません（${prevId} の後に ${p.id}）。`;
    }
    prevId = p.id;
  }

  const n = participants.length;
  if (n < 4) {
    return `参加者数 ${n} が4未満です。`;
  }
  if (n > 40) {
    return `参加者数 ${n} が40を超えています。Apps Script のフォーム作成は40人以下に対応しています。`;
  }

  return null;
}

/**
 * participants の内容から SHA-256 を計算する（§5.3）。
 * participants を id TAB name の行を LF でつないだ文字列の SHA-256。
 */
function _computeParticipantsDigest(participants) {
  const lines = participants
    .sort((a, b) => (a.id < b.id ? -1 : 1))
    .map((p) => p.id + "\t" + p.name)
    .join("\n");
  const bytes = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    lines,
    Utilities.Charset.UTF_8,
  );
  return bytes
    .map((b) => (b < 0 ? b + 256 : b).toString(16).padStart(2, "0"))
    .join("");
}

/**
 * form_config シートから特定の item_type の value を取得する。
 */
function _getFormConfigValue(sheet, itemType) {
  const data = sheet.getDataRange().getValues();
  for (const row of data) {
    if (row[0] === itemType) {
      return String(row[4]) || String(row[1]) || null;
    }
  }
  return null;
}

/**
 * form_config から form_id を取得する。
 */
function _getFormId(ss) {
  const configSheet = ss.getSheetByName(SHEET_FORM_CONFIG);
  if (!configSheet) return null;
  return _getFormConfigValue(configSheet, "form_id");
}

/**
 * Date を日本時間の ISO 8601 文字列に変換する（§5.4）。
 */
function _formatJST(date) {
  if (!date) return "";
  const jst = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    jst.getUTCFullYear() +
    "-" +
    pad(jst.getUTCMonth() + 1) +
    "-" +
    pad(jst.getUTCDate()) +
    "T" +
    pad(jst.getUTCHours()) +
    ":" +
    pad(jst.getUTCMinutes()) +
    ":" +
    pad(jst.getUTCSeconds()) +
    "+09:00"
  );
}
