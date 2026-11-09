export interface ProxyItem { id:number; host:string; port:number; protocol:string; status:string; ip_address:string|null; latency_ms:number|null; error_message:string|null; last_checked:string|null; has_credentials:boolean; profile_name?:string; checking?:boolean }
export interface FacebookPost {url:string;date:string}
export interface FacebookPage {id:number;name:string;url:string;status:string;checked_at:string|null;check_date:string|null;timezone:string|null;posts_today:number|null;posts:FacebookPost[];error_message:string|null}
export interface FacebookAccount {name:string|null;url:string|null;status:string;checked_at:string|null;check_date:string|null;timezone:string|null;fresh:boolean;error_message:string|null;pages:FacebookPage[];page_count?:number}
export interface OutlookState {email:string;status:string;message:string|null;updated_at:string;active:boolean}
export interface BmPortfolio {bm_id:string;name:string}
export interface BmProfileState {portfolios:BmPortfolio[];note:string;updated_at:string;mobile_enabled?:boolean}
export interface Profile { revision?:number; recipient_active?:boolean; id:number; name:string; profile_path:string; proxy_id:number|null; proxy:ProxyItem|null; browser_status:string; browser_mode?:'manual'|'mobile'|'automation'|null; browser_error:string|null; task:{id:number;status:string;progress:number}|null;facebook?:FacebookAccount|null;outlook?:OutlookState|null;bm?:BmProfileState|null }
export interface System { cpu_percent:number; memory_percent:number; memory_used_mb:number; memory_total_mb:number; memory_available_mb:number; total_profiles:number; running_browsers:number; queued_browsers:number; stopped_browsers:number; working_proxies:number; failed_proxies:number; total_proxies:number; running_tasks:number; pending_tasks:number; resource_warning:string|null; max_browsers:number; checking_proxies:number; proxy_routing?:'all'|'facebook_only' }
export interface TaskItem { id:number; name:string; status:string; actions:string[]; runs:number; active_runs:number; created_at:string }
export interface Run { id:number;task_id:number;profile_id:number|null;profile_name:string;status:string;error_message:string|null;progress:number;created_at:string;started_at:string|null;finished_at:string|null;result:Record<string,unknown>[] }
export interface Log { id:number;profile_id:number|null;level:string;message:string;created_at:string }
export interface SettingsData { revision:number; max_browsers:number;max_memory_percent:number;max_cpu_percent:number;min_available_memory_mb:number;action_delay_ms:number;launch_timeout_seconds:number;task_timeout_seconds:number;require_proxy:boolean;proxy_ip_version:'auto'|'ipv4'|'ipv6';proxy_routing:'all'|'facebook_only';allowed_origins:string[];facebook_auto_refresh:boolean }
export interface FacebookRefreshProfileStatus {profile_id:number;next_due_at:string|null;last_attempt_at:string|null;last_finished_at:string|null;result:string|null}
export interface FacebookRefreshStatus {enabled:boolean;interval_hours:24;running:boolean;eligible_count:number;due_count:number;next_due_at:string|null;last_finished_at?:string|null;active_profile_id?:number|null;profiles:FacebookRefreshProfileStatus[]}
export interface Action { type:string;url?:string;selector?:string;value?:string;pixels?:number;timeout_ms?:number }
export type Notify=(message:string,error?:boolean)=>void
export interface PageRecipientAddress {street?:string;city?:string;postal_code?:string;country?:string}
export interface PageRecipientSetup {avatar_path?:string;cover_path?:string;bio?:string;address?:PageRecipientAddress;categories?:string[]}
export interface PageRecipientStart {profile_id:number;expected_page_url?:string;expected_page_name?:string;outlook_email:string;code_wait_seconds:300;setup:PageRecipientSetup;accept_only?:boolean}
export interface PageRecipientField {status:string;message?:string|null}
export interface PageRecipientJob {profile?:Profile;id:string|number;profile_id:number;profile_name:string;status:string;step:string;message?:string|null;page_id?:string|null;page_name?:string|null;started_at:string;updated_at:string;code_deadline?:string|null;fields:Record<string,PageRecipientField>;active:boolean;candidates?:{name:string;url:string}[]}
export interface PageRecipientConfig {asset_root:string}
export interface PageRecipientMailCheck {found:boolean;message:string;diagnostics?:Record<string,boolean>}
export interface ReelsConfig {max_upload_bytes:number;accepted_extensions:string[];default_timezone:string;scheduler_running:boolean;background_browser?:boolean}
export interface ReelAsset {id:string;name:string;size:number;mime:string;created_at:string;preview_url:string}
export interface ReelPostingEligibility {page_id:string|null;posting_page_id?:string|null;can_publish:boolean;information_status?:'updated'|'not_updated';posting_block_reason:'page_information_required'|'page_identity_unverified'|'page_policy_unavailable'|null;posting_revision?:number}
export interface ReelPageOption extends ReelPostingEligibility {page_url:string;page_name:string;source:'facebook_scan'|'received'}
export interface ReelScheduleInput {request_id:string;profile_id:number;page_url:string;page_name?:string;asset_id:string;caption:string;scheduled_local:string;timezone:string;fold?:0|1}
export interface ReelSchedule {page_id?:string|null;can_publish?:boolean;posting_block_reason?:ReelPostingEligibility['posting_block_reason'];asset_preview_url?:string;id:number;profile_id:number;profile_name:string;page_url:string;page_name:string;asset_id:string;asset_name:string;asset_size:number;caption:string;scheduled_at:string;timezone:string;status:string;step:string;message:string;created_at:string;updated_at:string;started_at?:string|null;finished_at?:string|null;published_url?:string|null;verification_url:string|null;publish_attempted:boolean;can_verify_link:boolean;can_cancel:boolean;can_resume:boolean;can_archive:boolean;archived_at?:string|null;can_restore:boolean}
