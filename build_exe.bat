@echo off
REM ============================================================
REM  MEP-Parser 단일 .exe 빌드 스크립트
REM  - Python 설치된 개발 PC 에서 1회 실행 → dist\MEP-Parser.exe 생성
REM  - 생성된 exe 는 Python 없는 현장 PC 에서 더블클릭 실행
REM  - 권장 인터프리터: Python 3.11 (shapely/GEOS 휠 + PyInstaller 안정)
REM ============================================================
setlocal
cd /d "%~dp0"

REM Python 3.11 우선, 없으면 기본 python
py -3.11 -V >nul 2>&1 && (set "PY=py -3.11") || (set "PY=python")
echo [1/3] 빌드 의존성 설치 (%PY%)
%PY% -m pip install --upgrade ezdxf shapely pyinstaller pytest || goto :err

REM 깨진 .exe 를 만들지 않는다 — CI 없이 이 한 줄로 같은 효과를 낸다.
REM 게이트는 pytest 로 돌린다. `run_all.py` 는 픽스처를 못 주어 테스트 48개를
REM 건너뛰므로 게이트로 쓰기엔 반쪽이다(그것도 이제 스스로 실패로 끝난다).
echo [2/3] 테스트
%PY% -m pytest tests -q || goto :testerr

echo [3/3] PyInstaller 빌드
%PY% -m PyInstaller mep_parser.spec --noconfirm || goto :err

echo.
echo ============================================================
echo  빌드 완료: dist\MEP-Parser.exe
echo  스모크 테스트:  dist\MEP-Parser.exe --selftest
echo ============================================================
goto :eof

:testerr
echo.
echo [중단] 테스트 실패 — .exe 를 만들지 않습니다. 위 로그를 확인하세요.
exit /b 1

:err
echo.
echo [오류] 빌드 실패. 위 로그를 확인하세요.
exit /b 1
