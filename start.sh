#!/usr/bin/env bash
# ==============================================================
# 量化交易系统 - 一键启动脚本
# 用法:
#   ./start.sh              # 启动(默认demo模式)
#   ./start.sh --live       # 实盘模式
#   ./start.sh --check      # 仅健康检查
#   ./start.sh --backtest   # 运行回测
#   ./start.sh --monitor    # 监控面板
# ==============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

MODE="demo"
ACTION="start"

# 解析参数
for arg in "$@"; do
    case $arg in
        --live)   MODE="live" ;;
        --demo)   MODE="demo" ;;
        --check)  ACTION="check" ;;
        --backtest) ACTION="backtest" ;;
        --monitor) ACTION="monitor" ;;
        --help)   ACTION="help" ;;
    esac
done

if [ "$ACTION" = "help" ]; then
    echo -e "${CYAN}量化交易系统 - 启动脚本${NC}"
    echo ""
    echo "用法: ./start.sh [选项]"
    echo ""
    echo "选项:"
    echo "  --demo      使用模拟环境 (默认)"
    echo "  --live      使用实盘环境 ⚠️"
    echo "  --check     仅运行健康检查"
    echo "  --backtest  运行回测"
    echo "  --monitor   启动监控面板"
    echo "  --help      显示帮助"
    exit 0
fi

echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}       🏦  量化交易系统  -  Trading 212${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "  模式: ${GREEN}${MODE}${NC}"
echo -e "  动作: ${GREEN}${ACTION}${NC}"
echo -e "  杠杆: ${RED}🚫 禁止${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""

# === 1. 环境检查 ===
echo -e "${BLUE}[1/4] 检查Python环境...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 未安装${NC}"
    exit 1
fi
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "  ✅ Python ${PYTHON_VERSION}"

# === 2. 依赖检查 ===
echo -e "${BLUE}[2/4] 检查依赖包...${NC}"
MISSING=$(python3 -c "
deps = ['pandas','numpy','sqlalchemy','yfinance','httpx','loguru','ta','vaderSentiment','feedparser','apscheduler','yaml','dotenv']
missing = [d for d in deps if __import__('importlib').util.find_spec(d) is None]
print(','.join(missing))
" 2>/dev/null || echo "check_failed")

if [ "$MISSING" != "" ] && [ "$MISSING" != "check_failed" ]; then
    echo -e "  ${YELLOW}⚠️ 缺少依赖: ${MISSING}${NC}"
    echo -e "  ${BLUE}安装中...${NC}"
    pip install -r requirements.txt -q
    echo -e "  ✅ 依赖已安装"
elif [ "$MISSING" = "check_failed" ]; then
    echo -e "  ${YELLOW}⚠️ 依赖检查异常，尝试安装...${NC}"
    pip install -r requirements.txt -q
else
    echo -e "  ✅ 依赖齐全"
fi

# === 3. .env 检查 ===
echo -e "${BLUE}[3/4] 检查配置...${NC}"
if [ ! -f ".env" ]; then
    echo -e "  ${YELLOW}⚠️ .env 不存在，从模板创建${NC}"
    cp .env.example .env
    echo -e "  ${YELLOW}⚠️ 请编辑 .env 填入API密钥后重新启动${NC}"
fi

if [ "$MODE" = "live" ]; then
    echo -e "  ${RED}🚨 实盘模式! 请确认:${NC}"
    echo -e "  ${RED}  1. 策略已在demo环境验证通过${NC}"
    echo -e "  ${RED}  2. T212_API_KEY 已正确配置${NC}"
    echo -e "  ${RED}  3. T212_ENVIRONMENT=live${NC}"
    echo -e ""
    read -p "  确认启动实盘? (输入 YES 继续): " confirm
    if [ "$confirm" != "YES" ]; then
        echo -e "  ${YELLOW}已取消${NC}"
        exit 0
    fi
    export T212_ENVIRONMENT=live
fi

# === 4. 健康检查 ===
echo -e "${BLUE}[4/4] 运行健康检查...${NC}"
python3 health_check.py
HEALTH=$?

if [ $HEALTH -ne 0 ] && [ "$ACTION" != "check" ]; then
    echo -e "  ${RED}❌ 健康检查未通过，存在FAIL项${NC}"
    echo -e "  ${YELLOW}可使用 --check 查看详情${NC}"
    read -p "  忽略检查结果继续启动? (y/N): " ignore
    if [ "$ignore" != "y" ]; then
        exit 1
    fi
fi

# === 执行 ===
echo ""
case $ACTION in
    check)
        echo -e "${GREEN}✅ 健康检查完成${NC}"
        ;;
    backtest)
        echo -e "${CYAN}📊 启动回测...${NC}"
        python3 main.py --mode $MODE --action backtest
        ;;
    monitor)
        echo -e "${CYAN}🖥️ 启动监控面板...${NC}"
        python3 main.py --mode $MODE --action monitor
        ;;
    start)
        echo -e "${GREEN}🚀 启动量化交易系统...${NC}"
        echo -e "  按 Ctrl+C 停止"
        echo ""
        python3 main.py --mode $MODE --action start
        ;;
esac
