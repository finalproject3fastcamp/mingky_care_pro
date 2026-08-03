import type { NotificationEvent } from '../types/monitoring'

interface Props {
  notifications: NotificationEvent[]
}

const formatTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('ko-KR', { hour12: false })

export function NotificationArea({ notifications }: Props) {
  return (
    <div className="card">
      <div className="card-title">알림</div>
      {notifications.length === 0 ? (
        <div className="empty">알림 없음</div>
      ) : (
        <ul className="notifications">
          {notifications.map((n) => (
            <li key={n.id} className={`notification ${n.level}`}>
              <span className="notification-time">{formatTime(n.created_at)}</span>
              <span className="notification-message">{n.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
