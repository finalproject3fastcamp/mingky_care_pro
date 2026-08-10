# 계정에 맞는 ROS_DOMAIN_ID 를 export 한다.
#
# /etc/environment 는 머신 하나에 값 하나라 여러 사람이 쓰는 서버에서는 못 쓴다.
# 각 계정 .bashrc 의 **최상단**에서 부른다. Ubuntu .bashrc 는 상단에서 비대화형
# 셸을 조기 반환하므로, 그 아래에 두면 ssh 원격 명령·systemd·cron 에 적용되지
# 않고 노드가 도메인 0 으로 뜬다.
_mingky_conf=/etc/mingky/ros-domains.conf
if [ -r "$_mingky_conf" ]; then
    _mingky_id="$(awk -F: -v u="$(id -un)" '$1==u {print $2}' "$_mingky_conf")"
    [ -n "$_mingky_id" ] && export ROS_DOMAIN_ID="$_mingky_id"
    unset _mingky_id
fi
unset _mingky_conf
