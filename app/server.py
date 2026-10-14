"""显式数据目录作为进程身份的一部分，供 Windows 启停核验。"""
import argparse
from app.main import create_app

def main():
    import uvicorn
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--port',type=int,default=8510)
    args=parser.parse_args()
    from app.settings import Settings
    settings = Settings(data_dir=args.data_dir)
    settings.port = args.port
    uvicorn.run(create_app(settings=settings),host='127.0.0.1',port=args.port)

if __name__=='__main__': main()
