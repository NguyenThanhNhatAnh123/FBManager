class AppError(ValueError):
    def __init__(self,detail,code='invalid_operation',status_code=400):
        self.detail,self.code,self.status_code=detail,code,status_code
        super().__init__(detail)
