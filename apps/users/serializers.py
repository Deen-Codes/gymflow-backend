from django.contrib.auth import authenticate
from rest_framework import serializers
from .models import User, TrainerProfile, ClientProfile
from apps.workouts.models import WorkoutPlan


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        # Case-insensitive username match. Most users will type "Deen"
        # or "deen" interchangeably and expect it to work — Django's
        # default `authenticate()` is case-sensitive, so we resolve to
        # the canonical stored username first via __iexact, then auth.
        raw_username = attrs["username"].strip()
        password = attrs["password"]

        canonical = (
            User.objects
            .filter(username__iexact=raw_username)
            .values_list("username", flat=True)
            .first()
        )
        if canonical is None:
            raise serializers.ValidationError("Invalid username or password.")

        user = authenticate(username=canonical, password=password)
        if not user:
            raise serializers.ValidationError("Invalid username or password.")

        attrs["user"] = user
        return attrs


class UserMeSerializer(serializers.ModelSerializer):
    trainer_slug = serializers.SerializerMethodField()
    trainer_business_name = serializers.SerializerMethodField()
    trainer_id = serializers.SerializerMethodField()
    assigned_workout_plan_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "role",
            "trainer_slug",
            "trainer_business_name",
            "trainer_id",
            "assigned_workout_plan_id",
        ]

    def get_trainer_slug(self, obj):
        if obj.role == User.TRAINER and hasattr(obj, "trainer_profile"):
            return obj.trainer_profile.slug
        if obj.role == User.CLIENT and hasattr(obj, "client_profile"):
            return obj.client_profile.trainer.slug
        return None

    def get_trainer_business_name(self, obj):
        if obj.role == User.TRAINER and hasattr(obj, "trainer_profile"):
            return obj.trainer_profile.business_name
        if obj.role == User.CLIENT and hasattr(obj, "client_profile"):
            return obj.client_profile.trainer.business_name
        return None

    def get_trainer_id(self, obj):
        if obj.role == User.TRAINER and hasattr(obj, "trainer_profile"):
            return obj.trainer_profile.id
        if obj.role == User.CLIENT and hasattr(obj, "client_profile"):
            return obj.client_profile.trainer.id
        return None

    def get_assigned_workout_plan_id(self, obj):
        if obj.role == User.CLIENT and hasattr(obj, "client_profile") and obj.client_profile.assigned_workout_plan:
            return obj.client_profile.assigned_workout_plan.id
        return None


class ClientCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def create_client_for_trainer(self, trainer_user):
        if trainer_user.role != User.TRAINER or not hasattr(trainer_user, "trainer_profile"):
            raise serializers.ValidationError("Only trainers can create clients.")

        user = User.objects.create_user(
            username=self.validated_data["username"],
            email=self.validated_data["email"],
            password=self.validated_data["password"],
            role=User.CLIENT,
        )

        client_profile = ClientProfile.objects.create(
            user=user,
            trainer=trainer_user.trainer_profile,
        )

        return user, client_profile


class ClientListSerializer(serializers.ModelSerializer):
    trainer_id = serializers.SerializerMethodField()
    assigned_workout_plan_id = serializers.SerializerMethodField()
    assigned_workout_plan_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "role",
            "trainer_id",
            "assigned_workout_plan_id",
            "assigned_workout_plan_name",
        ]

    def get_trainer_id(self, obj):
        if hasattr(obj, "client_profile"):
            return obj.client_profile.trainer.id
        return None

    def get_assigned_workout_plan_id(self, obj):
        if hasattr(obj, "client_profile") and obj.client_profile.assigned_workout_plan:
            return obj.client_profile.assigned_workout_plan.id
        return None

    def get_assigned_workout_plan_name(self, obj):
        if hasattr(obj, "client_profile") and obj.client_profile.assigned_workout_plan:
            return obj.client_profile.assigned_workout_plan.name
        return None


class AssignWorkoutPlanSerializer(serializers.Serializer):
    client_user_id = serializers.IntegerField()
    workout_plan_id = serializers.IntegerField()

    def assign(self, trainer_user):
        if trainer_user.role != User.TRAINER or not hasattr(trainer_user, "trainer_profile"):
            raise serializers.ValidationError("Only trainers can assign workout plans.")

        try:
            client_user = User.objects.get(
                id=self.validated_data["client_user_id"],
                role=User.CLIENT,
                client_profile__trainer=trainer_user.trainer_profile
            )
        except User.DoesNotExist:
            raise serializers.ValidationError("Client not found for this trainer.")

        try:
            workout_plan = WorkoutPlan.objects.get(
                id=self.validated_data["workout_plan_id"],
                user=trainer_user
            )
        except WorkoutPlan.DoesNotExist:
            raise serializers.ValidationError("Workout plan not found for this trainer.")

        client_profile = client_user.client_profile
        client_profile.assigned_workout_plan = workout_plan
        client_profile.save()

        return client_user, client_profile
