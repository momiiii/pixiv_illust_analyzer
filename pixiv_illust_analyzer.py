# Pixivタグ共起解析GUIツール（検索方式選択機能付き）
import streamlit as st

# 必要なライブラリをインポート
from pixivpy3 import AppPixivAPI
from collections import Counter
import time
import matplotlib.pyplot as plt
import matplotlib

# 日本語フォント設定
import platform
system = platform.system()

if system == "Windows":
    matplotlib.rcParams['font.family'] = ['MS Gothic', 'Yu Gothic', 'Meiryo', 'DejaVu Sans']
elif system == "Darwin":  # macOS
    matplotlib.rcParams['font.family'] = ['Hiragino Sans', 'Yu Gothic', 'DejaVu Sans']
else:  # Linux
    matplotlib.rcParams['font.family'] = ['Noto Sans CJK JP', 'DejaVu Sans']

matplotlib.font_manager._load_fontmanager(try_read_cache=False)

# 状態初期化
def get_pixiv_api():
    if 'api' not in st.session_state:
        st.session_state.api = None
    return st.session_state.api

# ログイン処理
def pixiv_login(refresh_token):
    if not refresh_token:
        st.error("refresh_tokenを入力してください。")
        return
    
    api = AppPixivAPI()
    try:
        with st.spinner("Pixivにログイン中..."):
            api.auth(refresh_token=refresh_token)
            st.session_state.api = api
            st.session_state.logged_in = True
            st.success("✅ Pixivにログイン成功！")
    except Exception as e:
        st.session_state.api = None
        st.session_state.logged_in = False
        st.error(f"❌ Pixivログインに失敗しました: {str(e)}")
        st.info("refresh_tokenが正しいか確認してください。")

# 英語タグ判定と除外機能
def is_english_tag(tag):
    """タグが英語かどうかを判定"""
    import re
    
    # 空文字や短すぎるタグは除外しない
    if not tag or len(tag.strip()) < 2:
        return False
    
    tag = tag.strip()
    
    # 日本語文字（ひらがな、カタカナ、漢字）が含まれていれば日本語タグとして扱う
    japanese_chars = re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]', tag)
    if japanese_chars:
        return False
    
    # 数字のみの場合は除外しない（年号など）
    if tag.isdigit():
        return False
    
    # 英語の文字が80%以上を占める場合は英語タグとして判定
    english_chars = re.findall(r'[a-zA-Z]', tag)
    total_meaningful_chars = re.findall(r'[a-zA-Z\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]', tag)
    
    if len(total_meaningful_chars) == 0:
        return False
    
    english_ratio = len(english_chars) / len(total_meaningful_chars)
    return english_ratio >= 0.8

def filter_tags_by_language(tags, exclude_english=True):
    """言語設定に基づいてタグをフィルタリング"""
    if not exclude_english:
        return tags, 0
    
    filtered_tags = []
    english_count = 0
    
    for tag in tags:
        if is_english_tag(tag):
            english_count += 1
        else:
            filtered_tags.append(tag)
    
    return filtered_tags, english_count

# AI画像判定機能
def is_ai_generated(illust):
    """イラストがAI生成かどうかを判定（簡易版）"""
    try:
        # イラストの情報からAI関連の手がかりを探す
        if hasattr(illust, 'illust_ai_type') and illust.illust_ai_type == 2:
            return True
        
        # タグベースでの判定
        if hasattr(illust, 'tags'):
            ai_keywords = ['ai', 'ai生成', 'aiイラスト', 'stable diffusion', 'midjourney', 'novel ai', 'nai']
            for tag in illust.tags:
                if hasattr(tag, 'name'):
                    tag_name = tag.name.lower()
                    if any(keyword in tag_name for keyword in ai_keywords):
                        return True
                if hasattr(tag, 'translated_name') and tag.translated_name:
                    tag_trans = tag.translated_name.lower()
                    if any(keyword in tag_trans for keyword in ai_keywords):
                        return True
        
        return False
    except:
        return False

# リクエスト間隔を動的に調整する関数（ランダムジッター付き）
def get_request_interval(max_illusts):
    """取得件数に応じてリクエスト間隔を調整（最低1.5秒、ランダムジッター付き）"""
    import random
    
    base_interval = 1.5  # 最低1.5秒
    if max_illusts <= 100:
        base_interval = 1.5  # 100件以下: 1.5秒
    elif max_illusts <= 300:
        base_interval = 2.0  # 300件以下: 2秒
    elif max_illusts <= 500:
        base_interval = 2.5  # 500件以下: 2.5秒
    elif max_illusts <= 750:
        base_interval = 3.0  # 750件以下: 3秒
    else:
        base_interval = 3.5  # 1000件: 3.5秒
    
    # ランダムジッター追加（±20%の範囲）
    jitter = random.uniform(-0.2, 0.2) * base_interval
    final_interval = max(1.5, base_interval + jitter)  # 最低1.5秒を保証
    
    return final_interval

# エラー時の指数バックオフ機能
def exponential_backoff_request(api, request_func, max_retries=3, base_delay=2.0):
    """指数バックオフとRetry-After尊重機能付きのAPIリクエスト"""
    import random
    import time
    from requests.exceptions import RequestException
    
    for attempt in range(max_retries + 1):
        try:
            result = request_func()
            return result, None  # 成功時はエラーなし
            
        except Exception as e:
            error_message = str(e).lower()
            
            # 最後の試行の場合は諦める
            if attempt == max_retries:
                return None, f"最大リトライ回数({max_retries})に達しました: {str(e)}"
            
            # Retry-Afterヘッダーの確認（可能な場合）
            retry_after = None
            if hasattr(e, 'response') and e.response and hasattr(e.response, 'headers'):
                retry_after = e.response.headers.get('Retry-After')
            
            # 待機時間の計算
            if retry_after:
                try:
                    wait_time = float(retry_after)
                    st.warning(f"⏳ サーバーからRetry-After指示: {wait_time}秒待機中...")
                except:
                    wait_time = base_delay * (2 ** attempt)  # 指数バックオフ
            else:
                # 指数バックオフ（ランダムジッター付き）
                wait_time = base_delay * (2 ** attempt) + random.uniform(0, 1)
            
            # レート制限エラーの特別処理
            if any(keyword in error_message for keyword in ['rate limit', '429', 'too many requests']):
                wait_time = max(wait_time, 30)  # レート制限時は最低30秒
                st.warning(f"⚠️ レート制限検出。{wait_time:.1f}秒待機後にリトライします... (試行 {attempt + 1}/{max_retries})")
            else:
                st.warning(f"🔄 APIエラー発生。{wait_time:.1f}秒待機後にリトライします... (試行 {attempt + 1}/{max_retries})")
            
            time.sleep(wait_time)
    
    return None, "予期しないエラー"

def normalize_search_query(query):
    """検索クエリを正規化"""
    import re
    query = query.replace('　', ' ').replace('＋', '+').replace('，', ',')
    query = re.sub(r'[+,|]', ' ', query)
    query = re.sub(r'\s+', ' ', query.strip())
    return query

# R18キーワード検出
def detect_r18_content(search_query):
    """R18関連のキーワードを検出"""
    r18_keywords = [
        'r18', 'r-18', 'r_18', 'nsfw', '18禁', '成人向け', 'えっち', 'エロ',
        'nude', 'naked', 'sex', 'hentai', 'ecchi', 'adult', '大人',
        '裸', 'おっぱい', '巨乳', 'パンツ', 'パンチラ', 'セックス', 'ドm', 'ドs'
    ]
    
    query_lower = search_query.lower()
    for keyword in r18_keywords:
        if keyword in query_lower:
            return True
    return False

# 検索方式の説明を取得
def get_search_mode_description(search_mode):
    """検索方式の説明を返す"""
    descriptions = {
        "partial_match_for_tags": "🏷️ **タグ検索**：作品に付けられたタグのみを検索対象とします",
        "exact_match_for_tags": "🎯 **タグ完全一致**：タグと完全に一致するもののみを検索します",
        "title_and_caption": "📝 **タイトル・説明文検索**：作品のタイトルと説明文を検索対象とします",
        "text": "🔍 **全文検索**：タグ・タイトル・説明文すべてを検索対象とします（最も幅広い検索）"
    }
    return descriptions.get(search_mode, "不明な検索モード")

# タグ分析（検索方式選択機能付き）
def analyze_tags(api, search_query, max_illusts, search_mode="partial_match_for_tags"):
    if not api:
        st.error("APIが初期化されていません。再ログインしてください。")
        return []
    
    # デバッグ情報表示用のコンテナ
    debug_container = st.expander("🔍 詳細な処理状況（デバッグ情報）", expanded=False)
    
    # リクエスト間隔を取得
    request_interval = get_request_interval(max_illusts)
    
    with debug_container:
        st.write("**📋 処理開始情報:**")
        st.write(f"- 元の検索クエリ: `{search_query}`")
        st.write(f"- 検索方式: `{search_mode}`")
        st.write(f"- {get_search_mode_description(search_mode)}")
        st.write(f"- 最大取得件数: {max_illusts}件")
        st.write(f"- ベースリクエスト間隔: {request_interval:.1f}秒（ランダムジッター付き、最低1.5秒保証）")
        st.write(f"- エラー時の自動リトライ: 有効（指数バックオフ＋Retry-After尊重）")
    
    # 検索クエリを正規化
    normalized_query = normalize_search_query(search_query)
    search_tags = [tag.strip() for tag in normalized_query.split() if tag.strip()]
    
    with debug_container:
        st.write(f"- 正規化後のクエリ: `{normalized_query}`")
        st.write(f"- 分割されたタグ: {search_tags}")
        st.write(f"- タグ数: {len(search_tags)}")
    
    # R18検出
    is_r18 = detect_r18_content(search_query)
    if is_r18:
        st.warning("⚠️ R18コンテンツを検索しています。処理に時間がかかる場合があります。")
        with debug_container:
            st.write("- R18コンテンツ検出: **Yes**")
    else:
        with debug_container:
            st.write("- R18コンテンツ検出: No")
    
    all_tags = []
    processed_count = 0
    found_matching_illusts = 0
    api_calls = 0
    ai_filtered_count = 0
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        # 主要なタグから検索開始
        search_word = " ".join(search_tags)
        
        with debug_container:
            st.write(f"**🎯 検索実行情報:**")
            st.write(f"- 検索ワード（複数タグ結合）: `{search_word}`")
            st.write(f"- 元の分割タグ: {search_tags}")
            st.write(f"- 使用する検索方式: `{search_mode}`")
        
        # 検索パラメータ設定（検索方式を選択可能に）
        search_params = {
            "word": search_word,
            "search_target": search_mode,  # ← ここが選択可能になった！
            "sort": "popular_desc"
        }
        
        # AI画像除外設定の確認
        exclude_ai = st.session_state.get('exclude_ai', True)
        
        with debug_container:
            st.write(f"- 検索パラメータ: {search_params}")
            if exclude_ai:
                st.write(f"- AI画像除外: **有効** (後処理で判定)")
            else:
                st.write(f"- AI画像除外: 無効")
        
        next_qs = None
        page_count = 0
        max_pages = max(15, max_illusts // 20)  # 最大ページ数を増加
        
        with debug_container:
            st.write(f"- 最大ページ数: {max_pages}")
            st.write(f"- 予想処理時間: 約{int((max_pages * request_interval) / 60)}分{int((max_pages * request_interval) % 60)}秒")
            st.write("")
            debug_log = st.empty()
        
        while found_matching_illusts < max_illusts and page_count < max_pages:
            # 進捗状況をより詳細に表示
            elapsed_time = page_count * request_interval
            estimated_total_time = max_pages * request_interval
            progress_percentage = min((found_matching_illusts / max_illusts) * 100, 100)
            
            status_text.text(f"🔍 検索中... ページ{page_count + 1}/{max_pages} | "
                           f"該当作品: {found_matching_illusts}/{max_illusts} ({progress_percentage:.1f}%) | "
                           f"経過時間: {int(elapsed_time//60)}:{int(elapsed_time%60):02d}")
            
            # API呼び出し（エラーハンドリング強化版）
            if next_qs:
                result, error = exponential_backoff_request(
                    api, 
                    lambda: api.search_illust(**next_qs),
                    max_retries=3
                )
            else:
                result, error = exponential_backoff_request(
                    api, 
                    lambda: api.search_illust(**search_params),
                    max_retries=3
                )
            
            # エラーが発生した場合の処理
            if error:
                with debug_container:
                    debug_log.write(f"❌ ページ{page_count + 1}: APIエラー - {error}")
                st.error(f"APIエラーが発生しました: {error}")
                break
            
            json_result = result
            api_calls += 1
            
            with debug_container:
                debug_log.write(f"**ページ {page_count + 1} の結果:**\n"
                              f"- API呼び出し回数: {api_calls}\n"
                              f"- 取得できた作品数: {len(json_result.illusts) if json_result and hasattr(json_result, 'illusts') else 0}\n"
                              f"- 実際のリクエスト間隔: {request_interval:.1f}秒（ジッター込み）")
            
            if not json_result or not hasattr(json_result, 'illusts') or not json_result.illusts:
                with debug_container:
                    debug_log.write(f"❌ ページ{page_count + 1}: 検索結果が空です")
                break
            
            page_matching_count = 0
            page_processed_count = 0
            page_ai_filtered = 0
            
            # 各イラストをチェック
            for illust in json_result.illusts:
                if not hasattr(illust, 'tags'):
                    continue
                
                page_processed_count += 1
                
                # AI画像の除外判定
                if exclude_ai and is_ai_generated(illust):
                    ai_filtered_count += 1
                    page_ai_filtered += 1
                    continue
                
                # タグリストを取得
                illust_tags = []
                for tag in illust.tags:
                    if hasattr(tag, 'name'):
                        illust_tags.append(tag.name)
                    if hasattr(tag, 'translated_name') and tag.translated_name:
                        illust_tags.append(tag.translated_name)
                
                # タグ検索の場合のみ検索タグを除外、キーワード検索の場合は除外しない
                if search_mode in ["partial_match_for_tags", "exact_match_for_tags"]:
                    # タグ検索：検索タグを除外した他のタグを収集
                    filtered_tags = []
                    for tag in illust_tags:
                        tag_lower = tag.lower()
                        is_search_tag = False
                        
                        # 検索タグと一致するかチェック
                        for search_tag in search_tags:
                            search_tag_lower = search_tag.lower()
                            if (tag_lower == search_tag_lower or
                                search_tag_lower in tag_lower or 
                                tag_lower in search_tag_lower):
                                is_search_tag = True
                                break
                        
                        if not is_search_tag:
                            filtered_tags.append(tag)
                else:
                    # キーワード検索：全てのタグを収集（検索キーワードも含む）
                    filtered_tags = illust_tags
                
                # 言語フィルターを適用
                exclude_english = st.session_state.get('exclude_english', True)
                english_count = 0
                if exclude_english:
                    filtered_tags, english_count = filter_tags_by_language(filtered_tags, exclude_english)
                
                all_tags.extend(filtered_tags)
                found_matching_illusts += 1
                page_matching_count += 1
                
                # 最初の数件は詳細ログを表示
                if found_matching_illusts <= 5:
                    with debug_container:
                        debug_log.write(f"✅ 作品 {found_matching_illusts}: "
                                      f"収集タグ: {len(filtered_tags)}")
                        if exclude_english and english_count > 0:
                            debug_log.write(f"  - 英語タグ除外: {english_count}件")
                        # 作品のタグ一覧を表示（デバッグ用）
                        debug_log.write(f"  - 作品のタグ例: {illust_tags[:5]}")
                
                processed_count += 1
                if found_matching_illusts >= max_illusts:
                    break
            
            with debug_container:
                debug_log.write(f"- このページの該当作品: {page_matching_count}/{page_processed_count}")
                if exclude_ai and page_ai_filtered > 0:
                    debug_log.write(f"- このページのAI作品除外: {page_ai_filtered}件")
                debug_log.write(f"- 累計該当作品: {found_matching_illusts}, 累計収集タグ: {len(all_tags)}")
            
            progress_bar.progress(min(found_matching_illusts / max_illusts, 1.0))
            
            # 次のページへ
            next_qs = api.parse_qs(json_result.next_url) if hasattr(json_result, 'next_url') and json_result.next_url else None
            if not next_qs:
                with debug_container:
                    debug_log.write("ℹ️ 次のページがありません（検索終了）")
                break
            
            page_count += 1
            
            # 動的リクエスト間隔でサーバー負荷を軽減（ランダムジッター付き）
            if page_count < max_pages and found_matching_illusts < max_illusts:
                # 毎回新しいジッター付き間隔を取得
                current_interval = get_request_interval(max_illusts)
                time.sleep(current_interval)
                
                # 大量取得時の追加の配慮
                if max_illusts >= 500 and page_count % 10 == 0:
                    # 10ページごとに少し長めの休憩（ジッター付き）
                    import random
                    extra_wait = 3.0 + random.uniform(0, 2.0)
                    with debug_container:
                        debug_log.write(f"⏱️ 10ページ処理完了、サーバー負荷軽減のため追加休憩中...({extra_wait:.1f}秒)")
                    time.sleep(extra_wait)
    
    except Exception as e:
        st.error(f"データ取得中にエラーが発生しました: {str(e)}")
        with debug_container:
            st.write(f"**❌ エラー詳細:**")
            st.write(f"- エラーメッセージ: {str(e)}")
            st.write(f"- エラー発生時点での処理済み作品数: {processed_count}")
            st.write(f"- エラー発生時点での該当作品数: {found_matching_illusts}")
            st.write(f"- API呼び出し回数: {api_calls}")
        return []
    
    finally:
        progress_bar.empty()
        status_text.empty()
    
    # 最終結果のデバッグ情報
    with debug_container:
        st.write(f"**📊 最終結果:**")
        st.write(f"- 総処理作品数: {processed_count}")
        st.write(f"- 該当作品数: {found_matching_illusts}")
        st.write(f"- 収集タグ総数: {len(all_tags)}")
        st.write(f"- ユニークタグ数: {len(set(all_tags)) if all_tags else 0}")
        st.write(f"- API呼び出し回数: {api_calls}")
        st.write(f"- 処理ページ数: {page_count}")
        st.write(f"- 使用した検索方式: `{search_mode}`")
        st.write(f"- 平均リクエスト間隔: {request_interval:.1f}秒（ジッター込み）")
        st.write(f"- 総処理時間: 約{int((page_count * request_interval) // 60)}分{int((page_count * request_interval) % 60)}秒")
        
        # 言語・AI画像フィルターの結果を表示
        exclude_english = st.session_state.get('exclude_english', True)
        exclude_ai = st.session_state.get('exclude_ai', True)
        if exclude_english:
            st.write(f"- 英語タグ除外: **有効**")
        else:
            st.write(f"- 英語タグ除外: 無効")
        if exclude_ai:
            st.write(f"- AI画像除外: **有効** (除外数: {ai_filtered_count}件)")
        else:
            st.write(f"- AI画像除外: 無効")
    
    if not all_tags:
        st.warning(f"条件に一致するタグが見つかりませんでした。")
        st.info(f"📊 処理結果: {processed_count}作品を確認し、{found_matching_illusts}作品が条件に該当しました。")
        if exclude_ai and ai_filtered_count > 0:
            st.info(f"🤖 AI画像を{ai_filtered_count}件除外しました。")
        if found_matching_illusts == 0:
            st.info("💡 **解決のヒント**:")
            st.info("- タグ名のスペルを確認してください")
            st.info("- より一般的なタグで試してください") 
            st.info("- 単一のタグで検索してみてください")
            st.info("- R18タグの場合、ログイン状態を確認してください")
            st.info("- 検索方式を「全文検索」に変更してみてください")
        return []
    
    result_info = f"✅ {found_matching_illusts}件の該当作品から{len(all_tags)}個のタグを収集しました。"
    if exclude_ai and ai_filtered_count > 0:
        result_info += f" (AI画像{ai_filtered_count}件を除外)"
    st.info(result_info)
    
    counter = Counter(all_tags)
    return counter.most_common(30)

# Pixiv検索URLを生成する関数
def create_pixiv_search_url(original_query, additional_tag):
    """元の検索クエリと追加タグを組み合わせてPixiv検索URLを生成"""
    import urllib.parse
    
    # 元のクエリを正規化
    normalized_original = normalize_search_query(original_query)
    
    # 組み合わせクエリを作成
    combined_query = f"{normalized_original} {additional_tag}".strip()
    
    # URLエンコード
    encoded_query = urllib.parse.quote(combined_query)
    
    # Pixiv検索URL
    return f"https://www.pixiv.net/tags/{encoded_query}/artworks"

# クリック可能なタグテーブルを作成する関数
def create_clickable_tag_table(tag_data, original_query):
    """クリック可能なリンク付きのタグテーブルを作成"""
    if not tag_data:
        st.warning("表示するデータがありません。")
        return
    
    st.subheader("📋 クリック可能なタグ一覧")
    st.markdown("💡 **リンクをクリックすると、元の検索タグと組み合わせてPixivで検索できます**")
    
    # シンプルなテーブル表示（確実に動作する方法）
    import pandas as pd
    
    # 基本的なデータフレームを作成
    table_data = []
    urls_data = []  # URLを別で管理
    
    for i, (tag, count) in enumerate(tag_data[:20], 1):
        # 検索URLを生成
        search_url = create_pixiv_search_url(original_query, tag)
        combined_query = f"{normalize_search_query(original_query)} {tag}"
        
        table_data.append({
            "順位": i,
            "タグ名": tag,
            "使用回数": f"{count}回",
            "組み合わせ検索": f"🔍 Pixivで検索 ({combined_query})"
        })
        
        urls_data.append({
            "tag": tag,
            "url": search_url,
            "query": combined_query
        })
    
    # データフレームを表示
    df = pd.DataFrame(table_data)
    st.dataframe(df, use_container_width=True)
    
    # 個別のリンクボタンを表示
    st.markdown("---")
    st.markdown("**🔗 個別検索リンク:**")
    
    # 3列でボタンを配置
    cols_per_row = 3
    for i in range(0, len(urls_data), cols_per_row):
        cols = st.columns(cols_per_row)
        for j, col in enumerate(cols):
            if i + j < len(urls_data):
                data = urls_data[i + j]
                with col:
                    # Streamlitのlinkボタンを使用
                    if st.link_button(
                        f"🔍 {data['tag']}", 
                        data['url'],
                        help=f"「{data['query']}」でPixiv検索"
                    ):
                        pass  # リンクボタンなので何もしない
    
    # 使用方法の説明
    st.info("🔗 **使い方**: 上記のボタンをクリックすると、元の検索タグとそのタグを組み合わせてPixivで検索できます。新しいタブで開きます。")

# 円グラフ表示（改良版）
def plot_pie_chart(tag_data, original_query):
    if not tag_data:
        st.warning("表示するデータがありません。")
        return
    
    try:
        labels, counts = zip(*tag_data[:10])
        
        fig, ax = plt.subplots(figsize=(10, 8))
        colors = plt.cm.Set3(range(len(labels)))
        
        # 日本語ラベルの安全な処理
        safe_labels = []
        for label in labels:
            try:
                if any('\u3040' <= char <= '\u309F' or '\u30A0' <= char <= '\u30FF' or '\u4E00' <= char <= '\u9FAF' for char in label):
                    safe_label = label[:10] + ('...' if len(label) > 10 else '')
                else:
                    safe_label = label
                safe_labels.append(safe_label)
            except:
                safe_labels.append(f"Tag_{len(safe_labels)+1}")
        
        wedges, texts, autotexts = ax.pie(
            counts, 
            labels=safe_labels, 
            autopct='%1.1f%%', 
            startangle=90,
            colors=colors
        )
        
        for text in texts:
            text.set_fontsize(8)
        for autotext in autotexts:
            autotext.set_fontsize(8)
            autotext.set_color('white')
            autotext.set_weight('bold')
        
        ax.set_title(f"Tag Usage Frequency (Top {len(labels)})", fontsize=14, pad=20)
        
        st.pyplot(fig)
        plt.close(fig)
        
    except Exception as e:
        st.error(f"グラフの作成に失敗しました: {str(e)}")
        # フォールバック: 簡単なリスト表示
        for i, (tag, count) in enumerate(tag_data[:15], 1):
            st.write(f"{i}. {tag}: {count}回")

# メインGUI
st.set_page_config(
    page_title="Pixiv タグ共起分析ツール", 
    layout="centered",
    initial_sidebar_state="collapsed"
)

st.title("🎨 Pixiv タグ共起分析ツール（検索方式選択機能付き）")
st.markdown("**複数タグの組み合わせで、一緒によく使われるタグを分析します**")

# セッション状態の初期化
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

# 使い方説明
with st.expander("📖 使い方と新機能（検索方式選択）"):
    st.markdown("""
    **🆕 新機能: 検索方式選択**:
    - **タグ検索**: 作品に付けられたタグのみを検索（従来の方式）
    - **キーワード検索**: タイトル・説明文も含めて検索（Pixivウェブに近い）
    - **全文検索**: タグ・タイトル・説明文すべてを検索（最も幅広い）
    - **タグ完全一致**: タグと完全に一致するもののみ（最も厳密）
    
    **🔧 修正内容**:
    - **検索方式を選択可能に**: タグ検索とキーワード検索を選べるようになりました
    - **クリック可能な検索リンク機能**: タグをクリックして組み合わせ検索が可能
    - AI画像の判定を後処理で実行（タグベースでの判定）
    - 複数タグ検索の論理を改善
    - R18コンテンツの検索処理を最適化
    - **詳細なデバッグ情報を追加**
    
    **📝 使用方法**:
    1. **refresh_token を取得**: Pixivにログインして開発者ツールから取得
    2. **ログイン**: 上記のトークンを入力してログインボタンを押す
    3. **🆕 検索方式を選択**: タグ検索かキーワード検索かを選ぶ
    4. **検索**: 複数タグの場合は `タグA タグB` の形式で入力
    5. **分析開始**: ボタンを押して結果を待つ
    6. **🆕 組み合わせ検索**: 結果のタグをクリックして元のタグと組み合わせ検索！
    
    **🔍 検索方式の違い**:
    
    | 検索方式 | 検索対象 | 特徴 | おすすめ用途 |
    |----------|----------|------|-------------|
    | **タグ検索** | タグのみ | 従来の方式、精度高い | タグの共起関係を調べたい |
    | **キーワード検索** | タイトル・説明文 | 作品の内容を反映 | 特定のテーマ・ストーリーを探す |
    | **全文検索** | タグ・タイトル・説明文 | 最も幅広い検索 | できるだけ多くの作品を見つけたい |
    | **タグ完全一致** | タグ（完全一致） | 最も厳密 | 正確なタグ名で絞り込みたい |
    
    **🔗 新機能: クリック可能な検索リンク**:
    - 分析結果のタグをクリックすると、自動でPixiv検索画面に移動
    - 元の検索タグと組み合わせた検索が実行されます
    - 例: 「おっぱい」で分析→「オリジナル」をクリック→「おっぱい オリジナル」で検索
    - 新しいタブで開くので、分析結果を見ながら検索可能
    
    **🔍 デバッグ機能について**:
    - 分析実行時に「詳細な処理状況（デバッグ情報）」が表示されます
    - うまく動かない時は、この情報を確認してください：
      - どのタグで検索しているか
      - 何件の作品が見つかったか  
      - どこでエラーが起きているか
      - API呼び出しが成功しているか
      - 英語タグが何件除外されたか
      - AI画像が何件除外されたか
      - どの検索方式を使用しているか
    
    **🔤 英語タグ除外機能**:
    - 日本語（ひらがな・カタカナ・漢字）を含むタグのみを表示
    - 「anime」「cute」「girl」などの英語タグを自動で除外
    - より日本のPixivユーザーに関連性の高い結果が得られます
    
    **🤖 AI画像除外機能（修正版）**:
    - タグベースでAI生成画像を判定・除外
    - 「ai」「ai生成」「stable diffusion」などのタグを持つ作品を除外
    - より人間が描いた作品に特化したタグ分析が可能
    - API制限を回避するため後処理で判定
    
    **💡 検索のコツ**:
    - **検索件数を増やしたい場合**: 「全文検索」を選択
    - **精密なタグ関係を調べたい場合**: 「タグ検索」を選択
    - **作品の内容重視の場合**: 「キーワード検索」を選択
    - **⚠️ 大量取得時の注意**: 500件以上の取得には時間がかかります（15-40分程度）
    - **サーバー負荷軽減**: 最低1.5秒間隔＋ランダムジッター＋自動リトライ機能
    - **エラー時の自動対応**: 指数バックオフとRetry-After尊重でサーバーに優しく
    - R18系タグは最初は少ない取得数（30-50件）で試してください
    - 複数タグは関連性の高いものを組み合わせてください
    - うまくいかない場合は「デバッグ情報」を確認してください
    
    **例**: `ドMホイホイ R-18` や `初音ミク VOCALOID` や `猫 可愛い`
    """)

# ログインセクション
st.subheader("🔐 Pixivログイン")
refresh_token = st.text_input(
    "Pixiv refresh_token", 
    type="password",
    help="Pixivの開発者ツールから取得してください"
)

col1, col2 = st.columns([1, 1])
with col1:
    if st.button("🚀 ログイン", type="primary"):
        pixiv_login(refresh_token)

with col2:
    if st.session_state.get('logged_in', False):
        st.success("✅ ログイン済み")
    else:
        st.error("❌ ログインが必要")

# 検索セクション
st.markdown("---")
st.subheader("🔍 タグ分析")

# 🆕 検索方式選択
st.markdown("**🆕 検索方式選択**")
search_mode_options = {
    "partial_match_for_tags": "🏷️ タグ検索（部分一致）- 従来の方式",
    "exact_match_for_tags": "🎯 タグ完全一致 - より厳密",
    "title_and_caption": "📝 キーワード検索（タイトル・説明文）- 作品内容重視",
    "text": "🔍 全文検索（タグ・タイトル・説明文）- 最も幅広い"
}

search_mode = st.selectbox(
    "検索方式を選択してください",
    options=list(search_mode_options.keys()),
    format_func=lambda x: search_mode_options[x],
    index=0,  # デフォルトはタグ検索
    help="検索対象を選択できます。全文検索にするとPixivウェブサイトの検索に近い結果が得られます。"
)

# 選択した検索方式の説明を表示
st.info(get_search_mode_description(search_mode))

# フィルター設定
st.markdown("**🔧 フィルター設定**")
col_setting1, col_setting2 = st.columns([1, 1])

with col_setting1:
    exclude_english = st.checkbox(
        "🔤 英語タグを除外する", 
        value=st.session_state.get('exclude_english', True),
        help="チェックすると、結果から英語のタグを除外し、日本語タグのみを表示します"
    )
    st.session_state.exclude_english = exclude_english

with col_setting2:
    exclude_ai = st.checkbox(
        "🤖 AI画像を除外する", 
        value=st.session_state.get('exclude_ai', True),
        help="チェックすると、AI関連タグを持つ作品を検索対象から除外します（タグベース判定）"
    )
    st.session_state.exclude_ai = exclude_ai

# 設定状況の表示
col_status1, col_status2 = st.columns([1, 1])
with col_status1:
    if exclude_english:
        st.success("✅ 日本語タグのみ表示")
    else:
        st.info("ℹ️ 全言語のタグを表示")

with col_status2:
    if exclude_ai:
        st.success("✅ 人間作成の作品のみ")
    else:
        st.info("ℹ️ AI画像も含む")

col1, col2 = st.columns([2, 1])
with col1:
    tag_query = st.text_input(
        "検索タグ・キーワード", 
        value="ドMホイホイ R-18",
        help="検索方式に応じてタグまたはキーワードを入力してください。複数の場合はスペースで区切ってください。"
    )

with col2:
    max_count = st.selectbox(
        "最大取得数",
        options=[30, 50, 100, 200, 300, 500, 750, 1000],
        index=1,
        help="大きな数値ほど時間がかかります。R18関連は少ない数から始めることをお勧めします"
    )

if st.button("📊 分析開始", type="primary"):
    if not st.session_state.get('logged_in', False):
        st.warning("⚠️ 先にPixivへログインしてください。")
    elif not tag_query.strip():
        st.warning("⚠️ 検索タグ・キーワードを入力してください。")
    else:
        api = get_pixiv_api()
        if api:
            st.info(f"『{tag_query}』の分析を開始します...（検索方式: {search_mode_options[search_mode]}）")
            results = analyze_tags(api, tag_query, max_count, search_mode)
            
            if results:
                st.success(f"✅ 分析完了！{len(results)}件のタグが見つかりました。")
                
                # 結果表示
                st.subheader(f"📈 『{tag_query}』と一緒によく使われるタグ")
                
                # 使用した検索方式の表示
                st.markdown(f"**使用した検索方式**: {search_mode_options[search_mode]}")
                
                # クリック可能なタグテーブルを表示
                create_clickable_tag_table(results, tag_query)
                
                # 円グラフ表示
                st.subheader("🥧 使用頻度グラフ")
                plot_pie_chart(results, tag_query)
            else:
                st.error("❌ 条件に一致するデータが見つかりませんでした。")
                st.info("💡 より一般的なタグや、単一のタグで試してみてください。")
                st.info("💡 検索方式を「全文検索」に変更すると、より多くの結果が得られる可能性があります。")
        else:
            st.error("❌ API接続に問題があります。再ログインしてください。")

# フッター
st.markdown("---")
st.markdown("🛡️ **サーバー負荷軽減強化版**: 最低1.5秒間隔＋ランダムジッター＋指数バックオフ＋Retry-After尊重でPixivサーバーに優しい設計！")
