import cloudinary
import cloudinary.uploader

from fastapi import APIRouter, Depends, status, UploadFile, File, HTTPException, Security, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi_limiter.depends import RateLimiter
from sqlalchemy.orm import Session
from src.database.db import get_db
from src.database.models import User, Role
from src.repository import users as repository_users
from src.repository import profile as repository_profile
from src.services.auth import auth_service
from src.conf.config import settings
from src.services.cloud_image import CloudImage
from src.conf import messages
from typing import Optional
from src.services.role import allowed_admin_moderator, allowed_all_roles_access, allowed_admin
from src.schemas.users import UserResponse, UpdateFullProfile, ProfileResponse, ChangeRoleModel

router = APIRouter(prefix="/users", tags=["users"])
security = HTTPBearer()


@router.patch('/avatar', response_model=UserResponse)
async def update_avatar_user(file: UploadFile = File(),
                             current_user: User = Depends(auth_service.token_manager.get_current_user),
                             db: Session = Depends(get_db)):
    """
    The update_avatar_user function updates the avatar of a user.
        The function takes in an UploadFile object, which is a file that has been uploaded to the server.
        It also takes in a User object and Session object as dependencies.

    :param file: UploadFile: Upload the file to cloudinary
    :param current_user: User: Get the current user
    :param db: Session: Get the database session
    :return: A user object
    """
    cloudinary.config(
        cloud_name=settings.cloudinary_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True
    )
    public_id = CloudImage.generate_name_avatar(current_user.email)
    r = CloudImage.upload(file.file, public_id)
    src_url = CloudImage.get_url_for_avatar(public_id, r)
    user = await repository_users.update_avatar(current_user.email, src_url, db)
    return user


@router.patch(
              '/ban_user/{user_id}/{active_status}',
              response_model=UserResponse,
              dependencies=[Depends(allowed_admin_moderator)],
              description='Ban/unban user'
              )
async def ban_user(
                   user_id: int,
                   active_status: bool,
                   current_user: dict = Depends(auth_service.token_manager.get_current_user),
                   credentials: HTTPAuthorizationCredentials = Security(security),
                   db: Session = Depends(get_db)
                   ) -> User:
    """
    The ban_user function is used to ban a user from the system.

    :param user_id: int: Identify the user to be banned
    :param active_status: bool: Set the user's status to active or inactive
    :param current_user: dict: Get the current user from the auth user class
    :param db: Session: Access the database
    :return: The banned user object
    :doc-author: Trelent
    """
    user: Optional[User] = await repository_users.ban_user(user_id, active_status, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=messages.MSC404_USER_NOT_FOUND)

    await auth_service.token_manager.clear_user_cash(user.email)

    return user


@router.get("/me/",
            description="Get profile of current user.\nNo more than 5 requests per minute",
            dependencies=[
                  Depends(allowed_all_roles_access),
                  Depends(RateLimiter(times=5, seconds=60))
            ],
            status_code=status.HTTP_200_OK,
            response_model=ProfileResponse)
async def read_profile(
    current_user: User = Depends(auth_service.token_manager.get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    """
    Get profile of current user

    :param current_user: The current user.
    :type current_user: User
    :param db: Session: Connection to the database
    :return: The current user.
    :rtype: dict
    """
    result = await repository_profile.read_profile(current_user, db)
    return result


@router.patch("/me/",
              description='Updates profile of current user.\nNo more than 5 requests per minute',
              dependencies=[
                  Depends(allowed_all_roles_access),
                  Depends(RateLimiter(times=5, seconds=60))
              ],
              status_code=status.HTTP_200_OK,
              response_model=ProfileResponse)
async def update_profile(
    data: UpdateFullProfile,
    current_user: User = Depends(auth_service.token_manager.get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    """
    Updates profile of current user

    :param data: UpdateFullProfile: data to change
    :param current_user: The current user.
    :type current_user: User
    :param db: Session: Connection to the database
    :return: The current updated user.
    :rtype: dict
    """
    updated = await repository_profile.update_profile(data, current_user, db)
    if updated:
        result = await repository_profile.read_profile(current_user, db)
        return result
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=messages.USER_NOT_FOUND
    )


@router.patch("/change_role/",
              description='Updates role of user (only for admins).\nNo more than 5 requests per minute',
              dependencies=[
                  Depends(allowed_admin),
                  Depends(RateLimiter(times=5, seconds=60))
              ],
              status_code=status.HTTP_200_OK,
              response_model=ProfileResponse)
async def change_role(
    body: ChangeRoleModel,
    current_user: User = Depends(auth_service.token_manager.get_current_user),
    db: Session = Depends(get_db)
) -> ProfileResponse:
    """
    The update_profile_by_admin function allows an admin to update the role of a user.
        The function takes in the following parameters:
            - user_id: int, which is the id of the user whose profile will be updated.
            - role_user: Role, which is a new role for that specific user.

    :param body: ChangeRoleModel: id and role of a user to change.
    :param current_user: User: Get the user from the token
    :param db: Session: Create a connection to the database
    :return: A profile
    :doc-author: Trelent
    """
    if current_user.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=messages.MSC403_FORBIDDEN)
    if current_user.id == body.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=messages.MSC403_FORBIDDEN)
    user = await repository_users.get_user_by_id(body.user_id, db)
    if user:
        updated = await repository_profile.change_role(body.user_id, body.user_role, db)
        if updated:
            result = await repository_profile.read_profile(user, db)
            return result
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=messages.USER_ROLE_NOT_UPDATED
        )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=messages.USER_NOT_FOUND
    )


@router.get("/{username}/profile/", status_code=status.HTTP_200_OK)
async def read_profile_user(
    username: str = Path(min_length=2, max_length=16),
    current_user: User = Depends(auth_service.token_manager.get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get profile of selected user by their username

    :param username: username of user.
    :type current_user: str
    :return: The current user.
    :rtype: dict
    """
    if current_user.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=messages.MSC403_FORBIDDEN)
    user = await repository_users.get_user_by_username(username, db)
    if user and user.status_active is not None:
        result = await repository_profile.read_profile(user, db)
        return result
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=messages.USER_NOT_FOUND)


@router.get("/profil_all/", status_code=status.HTTP_200_OK)
async def read_profile_all_users(
    current_user: User = Depends(auth_service.token_manager.get_current_user),
    db: Session = Depends(get_db),
):

    """
    The read_profile_all_users function is used to read the profile of all users.
        This function can only be accessed by an admin user.
        The function returns a list of dictionaries containing the information for each user.

    :param current_user: User: Get the current user that is logged in
    :param db: Session: Get the database session
    :param : Get the current user
    :return: A list of all users
    :doc-author: Trelent
    """
    if current_user.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=messages.MSC403_FORBIDDEN)
    users = await repository_users.get_all_users(db)
    if not users:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=messages.USER_NOT_FOUND)
    return users
